// Bring an estate into a clone: choose where it comes from, then review what it would change (PG.3).
import { useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, fmt, getProjectSlug, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, Empty, ErrorNote, Field, Loading, Select, Tabs, useAction, useToast } from '../components/ui'
import ChangeSetView from '../studio/ChangeSetView'
import { ENVIRONMENT_OPTIONS, cloneSlug, slugProblem } from '../studio/edit'
import { SOURCE_LABEL } from '../studio/importing'
import '../studio/imports.css'

const NEW_CLONE = 'new'
const SOURCES = [['terraform_state', 'A Terraform state file'], ['connections', 'What your connections found'], ['repository', 'A repository']]
const here = (path) => `/p/${getProjectSlug()}${path}`

export default function ImportReview() {
  const { user } = useAuth()
  const [params] = useSearchParams()
  const set = params.get('set')
  const editing = allowed(user, 'edit_model')
  return set ? <ChangeSetView id={set} editing={editing} /> : <ImportHome editing={editing} />
}

function ImportHome({ editing }) {
  const imports = useApi('/api/imports')
  const clones = useApi('/api/clones')
  if ((imports.loading && !imports.data) || (clones.loading && !clones.data)) return <Loading rows={6} />
  if (imports.error) return <ErrorNote error={imports.error} retry={imports.reload} />
  if (clones.error) return <ErrorNote error={clones.error} retry={clones.reload} />
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div>
          <Link to={here('/studio')} className="muted">← Clone Studio</Link>
          <h2>Import an estate</h2>
          <p>Read an estate from where it is already written down, then decide what goes into a clone. Nothing reaches the clone until you accept it and apply it.</p>
        </div>
      </div>
      {editing
        ? <StartImport clones={clones.data.clones} onReload={clones.reload} />
        : <div className="banner info" role="status"><div>Your role can read imports but not start or decide them. Ask an engineer or a project admin.</div></div>}
      <History rows={imports.data.imports} />
    </div>
  )
}

function StartImport({ clones, onReload }) {
  const navigate = useNavigate()
  const toast = useToast()
  const found = useApi('/api/discoveries')
  const readable = useApi('/api/imports/sources')
  const [busy, run] = useAction()
  const [source, setSource] = useState('terraform_state')
  const [cloneId, setCloneId] = useState(clones[0]?.id ?? NEW_CLONE)
  const [name, setName] = useState('')
  const [environment, setEnvironment] = useState('prod')
  const [file, setFile] = useState(null)
  const [problem, setProblem] = useState('')
  const picker = useRef(null)
  const slug = cloneSlug(name)
  const options = [...clones.map((clone) => ({ value: clone.id, label: clone.clone, description: `${clone.environment}, version ${clone.version}` })),
    { value: NEW_CLONE, label: 'A new clone', description: 'Started empty, then filled by this import' }]
  const ready = { connections: Boolean(found.data?.sources.length), repository: Boolean(readable.data?.repository.ready) }[source] ?? Boolean(file)

  const request = async () => {
    if (source === 'connections' || source === 'repository') return { source }
    let state
    try { state = JSON.parse(await file.text()) } catch { throw new Error('That file is not a Terraform state: it is not valid JSON.') }
    return { source, label: `Terraform state, ${file.name}`, state }
  }
  const start = () => run('start', async () => {
    const stop = cloneId === NEW_CLONE ? slugProblem(slug) : ''
    if (stop) { setProblem(stop); return }
    const target = cloneId === NEW_CLONE ? { new_clone: { name: slug, environment } } : { clone_id: cloneId }
    const out = await api('/api/imports', { method: 'POST', body: { ...(await request()), ...target } })
    if (cloneId === NEW_CLONE) { onReload(); setCloneId(out.clone_id) }
    if (out.id) navigate(`?set=${out.id}`)
    else toast(out.message, 'info')
  })
  return (
    <Card title="Start an import">
      <div className="card-body import-start">
        <div className="stack">
          <Select id="import-clone" label="Into this clone" value={cloneId} onChange={(next) => { setCloneId(next); setProblem('') }} options={options} />
          {cloneId === NEW_CLONE && (
            <>
              <Field label="Name of the new clone" help={slug ? `Saved as ${slug}.` : 'A short name. Letters, numbers and hyphens.'}>
                <input id="import-name" className="input" autoComplete="off" value={name} onChange={(e) => { setName(e.target.value); setProblem('') }} />
              </Field>
              <Select id="import-environment" label="Environment" value={environment} onChange={setEnvironment} options={ENVIRONMENT_OPTIONS} />
            </>
          )}
          {problem && <p className="field-problem" role="alert">{problem}</p>}
        </div>
        <div className="stack import-source">
          <Tabs tabs={SOURCES} value={source} onChange={setSource} />
          {source === 'terraform_state' && <TerraformSource file={file} picker={picker} onPick={setFile} />}
          {source === 'connections' && <ConnectionsSource found={found} />}
          {source === 'repository' && <RepositorySource readable={readable} />}
        </div>
        <div className="import-go">
          <button className="btn primary" disabled={!ready || busy === 'start'} onClick={start}>
            {busy === 'start' ? <span className="spin" /> : 'Read it and compare'}</button>
          <span className="import-note">This only reads. You review every change before the clone is touched.</span>
        </div>
      </div>
    </Card>
  )
}

function TerraformSource({ file, picker, onPick }) {
  return (
    <div className="stack">
      <div className="import-file">
        <input ref={picker} type="file" accept="application/json,.json,.tfstate" hidden data-testid="state-file"
          onChange={(event) => { onPick(event.target.files?.[0] || null); event.target.value = '' }} />
        <button className="btn" onClick={() => picker.current?.click()}>{file ? 'Choose another file' : 'Choose a state file'}</button>
        <span className={file ? '' : 'faint'}>{file ? `${file.name}, ${fmt.n(Math.round(file.size / 1024))} KB` : 'No file chosen'}</span>
      </div>
      <p className="import-note">
        The file is sent once and not kept. Only the resources, their settings and how they connect are read. Values that look like
        secrets, such as tokens and passwords, are left out and never reach a clone.
      </p>
    </div>
  )
}

function ConnectionsSource({ found }) {
  if (found.loading && !found.data) return <Loading rows={2} />
  if (found.error) return <ErrorNote error={found.error} retry={found.reload} />
  const sources = found.data.sources
  if (!sources.length) {
    return (
      <p className="import-note">Nothing found yet. Open the <Link to={here('/studio?connection=aws')}>Studio</Link>, drag in an AWS connection, connect it and choose Find resources.</p>
    )
  }
  return (
    <div className="stack">
      <ul className="import-found">{sources.map((source) => (
        <li key={source.id}>{source.name} found {source.nodes} {source.nodes === 1 ? 'resource' : 'resources'} <time dateTime={source.discovered_at}>{fmt.ago(source.discovered_at)}</time>.</li>))}</ul>
      <p className="import-note">The resources that this project&apos;s connections can see are compared with the clone. Import a Terraform state first when you have one: a resource already in the clone is matched by its address and not added twice.</p>
    </div>
  )
}

function RepositorySource({ readable }) {
  if (readable.loading && !readable.data) return <Loading rows={2} />
  if (readable.error) return <ErrorNote error={readable.error} retry={readable.reload} />
  const { ready, why, repos } = readable.data.repository
  if (!ready) {
    return <p className="import-note">{why} Open the <Link to={here('/studio?connection=github')}>Studio</Link> and drag in a GitHub connection to connect it.</p>
  }
  return (
    <div className="stack">
      <p className="import-note">
        {repos.length ? `Reads ${repos.join(', ')}.` : 'Reads every repository the WhiteHats GitHub App can see.'} The Terraform, the manifests and
        lock files, the Dockerfiles and the workflows say which services run, in what, what they deploy and how the pipeline reaches your cloud.
      </p>
      <p className="import-note">This only reads. Nothing is written to GitHub, and the files are not kept: only what they say about the estate.
        Import a Terraform state first when you have one: what is already in the clone is matched by name and only filled in, never added twice.</p>
    </div>
  )
}

const STATUS = { open: ['info', 'Waiting for decisions'], applied: ['good', 'Applied'], discarded: ['neutral', 'Discarded'] }

function History({ rows }) {
  if (!rows.length) {
    return <Card title="Imports"><Empty title="No import yet">Choose a source above. The first import of an estate usually adds most of the clone.</Empty></Card>
  }
  return (
    <Card title="Imports" className="import-history">
      <div className="tbl-wrap tbl-stack">
        <table className="t">
          <thead><tr><th>Import</th><th>Clone</th><th>When</th><th>Changes</th><th>Status</th></tr></thead>
          <tbody>{rows.map((row) => {
            const [tone, label] = STATUS[row.status]
            return (
              <tr key={row.id}>
                <td data-label="Import"><Link to={`?set=${row.id}`}>{row.label}</Link>{!row.label.startsWith(SOURCE_LABEL[row.source]) && <div className="faint" style={{ fontSize: 12 }}>{SOURCE_LABEL[row.source] || row.source}</div>}</td>
                <td data-label="Clone">{row.clone}</td>
                <td data-label="When"><time dateTime={row.created_at}>{fmt.ago(row.created_at)}</time><div className="faint" style={{ fontSize: 12 }}>{row.created_by}</div></td>
                <td data-label="Changes" className="num">{row.status === 'open'
                  ? `${row.accepted + row.rejected} of ${row.changes} decided` : `${row.changes}${row.applied_version ? `, version ${row.applied_version}` : ''}`}</td>
                <td data-label="Status"><span className={`pill ${tone}`}>{label}</span></td>
              </tr>
            )
          })}</tbody>
        </table>
      </div>
    </Card>
  )
}
