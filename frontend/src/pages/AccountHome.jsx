import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, rememberProjects, useAuth } from '../App'
import { Card, Empty, ErrorNote, Field, Loading, Modal, Select } from '../components/ui'

const START = [
  { value: 'empty', label: 'Empty' },
  { value: 'health', label: 'Health template' },
  { value: 'fintech', label: 'Fintech template' },
  { value: 'connection', label: 'A connection' },
]
const CRITICALITY = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]
const HEALTH = { ok: 'Healthy', error: 'Needs attention', none: 'No connection' }
const ATTACHABLE = new Set(['Cloud logs', 'Code', 'CI/CD'])

export default function AccountHome() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi('/api/projects')
  const roll = useApi('/api/account/overview')
  const connectors = useApi('/api/connectors')
  const archived = useApi('/api/projects/archived')
  const [open, setOpen] = useState(false)
  const canCreate = allowed(user, 'manage_projects')

  if (error) return <ErrorNote error={error} retry={reload} />
  if (loading && !data) return <Loading rows={4} />
  if (data) rememberProjects(data)
  const rows = data || []
  const live = Object.fromEntries((roll.data?.projects || []).map((project) => [project.slug, project]))
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div>
          <h2>Your projects</h2>
          <p>Each project has its own score, findings and connections.</p>
        </div>
        {canCreate && <button className="btn primary" onClick={() => setOpen(true)}>New project</button>}
      </div>
      <AccountScore roll={roll} />
      {rows.length === 0 ? <Empty title="No projects yet">Create one to give a team its own estate.</Empty> : (
        <div className="project-grid">
          {rows.map((project) => <ProjectCard key={project.slug} project={project} live={live[project.slug]} />)}
        </div>
      )}
      {archived.data?.length > 0 && (
        <section className="stack" style={{ gap: 10 }} aria-labelledby="archived-projects">
          <div>
            <h3 id="archived-projects">Archived projects</h3>
            <p className="muted">Read-only. Nothing feeds them, and they are not in the account score.</p>
          </div>
          <div className="project-grid">
            {archived.data.map((project) => (
              <ArchivedCard key={project.slug} project={project} canRestore={canCreate}
                onRestored={() => { archived.reload(); reload(); roll.reload() }} />
            ))}
          </div>
        </section>
      )}
      {open && (
        <NewProject
          connectors={(connectors.data || []).filter((row) => ATTACHABLE.has(row.category))}
          onClose={() => setOpen(false)}
          onCreated={(project) => navigate(`/p/${project.slug}`)}
        />
      )}
    </div>
  )
}

function AccountScore({ roll }) {
  if (roll.error) return <ErrorNote error={roll.error} retry={roll.reload} />
  if (!roll.data) return <Loading rows={2} />
  const { account, lowest, findings, projects } = roll.data
  return (
    <Card className="account-score" title="Account score" hint="High counts three times, medium twice and low once." pad>
      <p><b>{account.score}</b> out of 1000 ({account.grade}).</p>
      {lowest && <p>Lowest project: {lowest.name}, score {lowest.score}.</p>}
      <p>Findings counted once across projects: {fmt.n(findings.account)}.</p>
      <ul aria-label="Project scores">
        {projects.map((project) => (
          <li key={project.slug}>{project.name}: {project.score} ({project.criticality}){project.in_account_score === false
            && <span className="muted">. Gathers other projects, so it is not weighed again</span>}</li>
        ))}
      </ul>
    </Card>
  )
}

function ProjectCard({ project, live }) {
  const score = live ? live.score : (project.score_measured ? project.score : null)
  return (
    <Link className="project-card" to={`/p/${project.slug}`}>
      <b>Open {project.name}</b>
      <div className="project-meta">
        <div><span>Score</span>{score == null ? 'Not measured yet' : score}</div>
        <div><span>Criticality</span>{label(project.criticality)}</div>
        <div><span>Open findings</span>{fmt.n(project.open_findings)}</div>
        <div><span>Connections</span>{HEALTH[project.connection_health] || project.connection_health}</div>
        <div><span>Last activity</span>{project.last_activity
          ? <time dateTime={project.last_activity}>{fmt.ago(project.last_activity)}</time>
          : 'No activity yet'}</div>
      </div>
    </Link>
  )
}

function ArchivedCard({ project, canRestore, onRestored }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const restore = async () => {
    setBusy(true)
    setError(null)
    try {
      await api(`/api/projects/${project.slug}`, { method: 'PATCH', body: { archived: false } })
      onRestored()
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }
  return (
    <div className="project-card archived">
      <Link to={`/p/${project.slug}`}><b>Open {project.name}</b></Link>
      <div className="project-meta">
        <div><span>State</span>Archived, read-only</div>
        <div><span>Open findings</span>{fmt.n(project.open_findings)}</div>
      </div>
      {canRestore && <button className="btn" onClick={restore} disabled={busy}>{busy ? 'Restoring' : `Restore ${project.name}`}</button>}
      {error && <ErrorNote error={error} />}
    </div>
  )
}

function label(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : ''
}

function NewProject({ connectors, onClose, onCreated }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState('empty')
  const [criticality, setCriticality] = useState('medium')
  const [connectorId, setConnectorId] = useState(connectors[0] ? String(connectors[0].id) : '')
  const [region, setRegion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async () => {
    setBusy(true)
    setError('')
    const body = { name, criticality, kind: 'empty' }
    if (kind === 'health' || kind === 'fintech') {
      body.kind = 'template'
      body.template = kind
    } else if (kind === 'connection') {
      body.kind = 'connection'
      body.connector_id = Number(connectorId)
      body.scope = region.trim() ? { regions: [region.trim()] } : {}
    }
    try {
      onCreated(await api('/api/projects', { method: 'POST', body }))
    } catch (exc) {
      setError(exc.message)
      setBusy(false)
    }
  }
  return (
    <Modal title="New project" onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !name.trim() || (kind === 'connection' && !connectorId)} onClick={save}>
          {busy ? <span className="spin" /> : 'Create project'}
        </button></>}>
      <div className="stack" style={{ gap: 12 }}>
        {error && <div className="banner bad" role="alert">{error}</div>}
        <Field label="Name"><input id="project-name" className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Start from"><Select id="start-from" value={kind} onChange={setKind} options={START} /></Field>
        <Field label="Criticality"><Select id="project-criticality" value={criticality} onChange={setCriticality} options={CRITICALITY} /></Field>
        {kind === 'connection' && (
          <>
            <Field label="Connection">
              <Select id="project-connection" value={connectorId} onChange={setConnectorId}
                options={connectors.map((row) => ({ value: String(row.id), label: row.name }))} />
            </Field>
            <Field label="Region" help="Leave this blank to take every region the connection reads.">
              <input id="project-region" className="input" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="ap-southeast-2" />
            </Field>
          </>
        )}
      </div>
    </Modal>
  )
}
