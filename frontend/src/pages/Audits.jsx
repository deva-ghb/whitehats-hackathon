// Security audits (AUD.6): every audit of the project with what started it, and one audit as it runs: the six steps,
// what each agent is doing, how much of the code it has covered, and what it confirmed, could not settle, or threw out.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, Empty, ErrorNote, Field, Icon, Loading, Modal, Sev, Status, useAction } from '../components/ui'

const TRIGGERS = {
  onboarding: 'First audit, when the code was connected',
  connections: 'New connections',
  cloud_change: 'The live cloud account changed',
  code_change: 'A pull request changed the code',
  schedule: 'Weekly audit',
  request: 'Asked for by a person',
}
const STEPS = [
  ['map', 'Map', 'Read the code and plan what to cover'],
  ['hunt', 'Hunt', 'One specialist per kind of weakness'],
  ['critic', 'Check coverage', 'Look for anything the hunt missed'],
  ['verify', 'Disprove', 'A fresh agent tries to knock down every lead'],
  ['prove', 'Prove', 'A test that fails while the weakness is there'],
  ['done', 'Report', 'What was found, fixed and not yet covered'],
]
const STEP_OF = { start: 'map', map: 'map', hunt: 'hunt', critic: 'critic', verify: 'verify', findings: 'verify',
  prove: 'prove', done: 'done', ledger: null, agent: null, budget: null }
const UNIT_TONE = { covered: 'good', candidate: 'warn', blocked: 'neutral', deferred: 'neutral', planned: 'neutral',
  in_progress: 'info', out_of_scope: 'neutral' }
const UNIT_LABEL = { covered: 'Covered', candidate: 'Lead found', blocked: 'Blocked', deferred: 'Not covered yet',
  planned: 'Waiting', in_progress: 'Being read', out_of_scope: 'Out of scope' }

export default function Audits() {
  return <SecurityAudits />
}

function SecurityAudits() {
  const { user, org } = useAuth()
  const { data, error, loading, reload } = useApi('/api/audits', { poll: 5000 })
  const [chosen, setChosen] = useState(null)
  const [asking, setAsking] = useState(false)
  const [busy, run] = useAction()
  if (loading && !data) return <Loading rows={4} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const audits = data.audits
  const open = chosen ?? audits[0]?.id
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div>
          <h2>Security audits</h2>
          <p>A full audit of your code and its cloud setup, by a team of specialist agents. It runs on its own when the
            code is first connected and again when the live account changes, and a fresh agent tries to disprove every
            lead before anything is called a finding.</p>
        </div>
        {allowed(user, 'triage') && (
          <button className="btn primary" onClick={() => setAsking(true)}><Icon.search size={15} /> Audit now</button>
        )}
      </div>
      <RunningNow audits={audits.filter((a) => ['queued', 'running', 'cancelling'].includes(a.status))} mayCancel={allowed(user, 'triage')}
        busy={busy} onCancel={(a) => run(`cancel-${a.id}`, () => api(`/api/audits/${a.id}/cancel`, { method: 'POST' }),
          a.status === 'queued' ? 'Cancelled' : 'Cancelling: it stops at its next step').then(reload)} onOpen={setChosen} />
      {!data.running && (
        <Card pad><p className="faint">Audits are recorded here but do not run on this server: its AI models for audits are not set up.</p></Card>
      )}
      {audits.length === 0
        ? <Card pad><Empty title="No audits yet">The first audit starts by itself once this project's code is connected.</Empty></Card>
        : (
          <div className="audit-layout">
            <div className="audit-list" role="list" aria-label="Audits">
              {audits.map((a) => (
                <button key={a.id} role="listitem" className={`audit-item ${a.id === open ? 'on' : ''}`} onClick={() => setChosen(a.id)}>
                  <span className="audit-item-head">
                    <strong>{TRIGGERS[a.trigger] || a.trigger}</strong>
                    <Status s={a.status === 'complete' ? 'done' : a.status} />
                  </span>
                  <span className="faint">{fmt.ago(a.created_at)}{a.repo ? ` · ${a.repo}` : ''}</span>
                  {a.status === 'skipped' && <span className="faint">Same connections as the last audit</span>}
                  {a.status === 'complete' || a.status === 'incomplete'
                    ? <span className="faint">{a.counts.confirmed ?? 0} confirmed · {a.coverage_percent}% covered · ${a.cost_usd.toFixed(2)}</span>
                    : a.status === 'running' && <span className="faint">Now: {stepName(a.step)}</span>}
                </button>
              ))}
            </div>
            {open && <AuditDetail key={open} id={open} />}
          </div>
        )}
      {asking && <AskModal local={org?.local_workspace} onClose={() => setAsking(false)} onStart={(id) => { setChosen(id); setAsking(false); reload() }} />}
    </div>
  )
}

const stepName = (step) => STEPS.find(([key]) => key === (STEP_OF[step] ?? step))?.[1] || step

/** AUD.7: what is waiting or running now, each with a way to stop it. */
function RunningNow({ audits, mayCancel, busy, onCancel, onOpen }) {
  if (!audits.length) return null
  return (
    <Card pad title="Running now" hint="Audits waiting or in progress. A cancelled one stops at its next step; nothing it found so far is kept.">
      <ul className="audit-running">
        {audits.map((a) => (
          <li key={a.id}>
            <button type="button" className="audit-running-what" onClick={() => onOpen(a.id)}>
              <strong>{TRIGGERS[a.trigger] || a.trigger}</strong>
              <span className="faint">{a.trigger_detail || a.repo}</span>
            </button>
            <span className="audit-running-state">
              <span className="row">
                {a.status === 'running' && <span className="spin" aria-hidden="true" />}
                <Status s={a.status} />
              </span>
              <span className="faint">{a.status === 'running' ? `Now: ${stepName(a.step)} · started ${fmt.ago(a.started_at)}` : a.status === 'queued' ? `Queued ${fmt.ago(a.created_at)}` : 'Stopping'}</span>
            </span>
            {mayCancel && a.status !== 'cancelling' && (
              <button type="button" className="btn small" disabled={busy === `cancel-${a.id}`} onClick={() => onCancel(a)}>Cancel</button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

function AuditDetail({ id }) {
  const { data, error, loading, reload } = useApi(`/api/audits/${id}`, { poll: 4000 })
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  if (data.status === 'skipped') {
    return (
      <div className="stack audit-detail" style={{ gap: 16 }}>
        <Card pad title="Not run: these connections were already audited" action={<Status s="skipped" />}>
          <p>{data.trigger_detail}</p>
          <p className="faint">A full audit starts by itself when a connection brings something the last one did not read: another AWS account or role, or a repository it did not see.</p>
        </Card>
      </div>
    )
  }
  const confirmed = data.records.filter((r) => r.verdict === 'confirmed')
  const open = data.records.filter((r) => r.verdict === 'needs_validation')
  const rejected = data.records.filter((r) => r.verdict === 'rejected')
  const fixes = Object.fromEntries(data.findings.map((f) => [f.fingerprint.replace(/^audit:/, ''), f]))
  return (
    <div className="stack audit-detail" style={{ gap: 16 }}>
      <Card pad title={TRIGGERS[data.trigger] || data.trigger} hint={data.trigger_detail || data.repo}
        action={<Status s={data.status === 'complete' ? 'done' : data.status} />}>
        {data.instructions && <div className="stack" style={{ marginBottom: 12, gap: 4 }}>
          <b>Audit instructions</b>
          <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: 0 }}>{data.instructions}</p>
        </div>}
        <Steps data={data} />
        <div className="audit-facts">
          <Fact label="Confirmed" value={data.counts.confirmed ?? confirmed.length} />
          <Fact label="Needs checking" value={data.counts.needs_validation ?? open.length} />
          <Fact label="Disproved" value={data.counts.rejected ?? rejected.length} />
          <Fact label="Covered" value={`${data.coverage_percent}%`} />
          <Fact label="Cost" value={`$${data.cost_usd.toFixed(2)}`} />
        </div>
        {data.error && <p className="audit-error">{data.error}</p>}
      </Card>
      {confirmed.length > 0 && (
        <Card pad title="Confirmed" hint="Each one was shown in a sealed test and survived an agent trying to disprove it">
          <ul className="audit-records">
            {confirmed.map((r) => (
              <li key={r.fingerprint}>
                <Sev s={r.severity} />
                <span className="audit-record-text"><strong>{r.title}</strong><span className="faint">{r.where}</span></span>
                {fixes[r.fingerprint]?.fix
                  ? <Link className="btn small" to="/approvals">Fix ready to approve</Link>
                  : <span className="faint">No fix offered</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {open.length > 0 && (
        <Card pad title="Needs checking" hint="Real leads that depend on something the code alone cannot show">
          <ul className="audit-records">
            {open.map((r) => (
              <li key={r.fingerprint}>
                <span className="pill info">Open</span>
                <span className="audit-record-text"><strong>{r.title}</strong><span className="faint">{r.blockers[0]}</span></span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {rejected.length > 0 && (
        <Card pad title="Disproved" hint="Leads a fresh agent knocked down, and why. They are not findings.">
          <ul className="audit-records">
            {rejected.map((r) => (
              <li key={r.fingerprint}>
                <span className="pill neutral">Disproved</span>
                <span className="audit-record-text"><strong>{r.title}</strong><span className="faint">{r.reason}</span></span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {data.units.length > 0 && (
        <Card pad title="What it covered" hint="Each area is one way in, one control, and one kind of weakness">
          <ul className="audit-units">
            {data.units.map((u, i) => (
              <li key={i}>
                <span className={`pill ${UNIT_TONE[u.status] || 'neutral'}`}>{UNIT_LABEL[u.status] || u.status}</span>
                <span className="audit-record-text"><strong>{u.surface}</strong>
                  <span className="faint">{u.attack_class} · {u.boundary}</span></span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card pad title="As it happened">
        {data.events.length
          ? <ol className="audit-events">{data.events.slice().reverse().map((e, i) => (
            <li key={i}><span className="faint">{fmtSeconds(e.at)}</span><span>{e.message}</span></li>
          ))}</ol>
          : <p className="faint">{data.status === 'failed' ? 'It stopped before its first step. The reason is above.' : data.status === 'queued' ? 'Waiting to start.' : 'Nothing recorded.'}</p>}
      </Card>
    </div>
  )
}

function Steps({ data }) {
  const current = STEP_OF[data.step] ?? (data.status === 'complete' ? 'done' : null)
  const reached = STEPS.findIndex(([key]) => key === current)
  const finished = data.status === 'complete' || data.status === 'incomplete'
  return (
    <ol className="audit-steps" aria-label="Audit steps">
      {STEPS.map(([key, label, hint], i) => {
        const state = finished || i < reached ? 'done' : i === reached && data.status === 'running' ? 'now' : 'later'
        return (
          <li key={key} className={state} aria-current={state === 'now' ? 'step' : undefined}>
            <span className="audit-step-dot">{state === 'done' ? <Icon.tick size={12} /> : state === 'now' ? <span className="spin audit-stage-spinner" aria-hidden="true" /> : i + 1}</span>
            <span className="audit-step-text"><strong>{label}</strong><span className="faint">{hint}</span></span>
          </li>
        )
      })}
    </ol>
  )
}

const Fact = ({ label, value }) => (
  <div className="audit-fact"><span className="faint">{label}</span><strong>{value}</strong></div>
)

function fmtSeconds(at) {
  const s = Math.round(at || 0)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}

function AskModal({ local, onClose, onStart }) {
  const [reason, setReason] = useState('')
  const [focus, setFocus] = useState('')
  const [busy, run] = useAction()
  const go = () => run('audit', async () => {
    const body = local ? { reason, instructions: focus } :
      { reason, scope: focus.split(',').map((folder) => folder.trim()).filter(Boolean) }
    const audit = await api('/api/audits', { method: 'POST', body })
    onStart(audit.id)
    return audit
  }, (audit) => audit.status === 'complete' ? 'Audit complete' : 'Audit queued')
  return (
    <Modal title="Audit now" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy} onClick={go}>{busy ? 'Queueing' : 'Start the audit'}</button>
    </>}>
      <Field label="Why are you running this audit?" help="For example, to prepare for a customer compliance review.">
        <input id="audit-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
      </Field>
      {local ? (
        <Field label="What should the audit focus on? (optional)">
          <textarea id="audit-focus" className="input" rows={3}
            placeholder="For example, review how the patient portal handles access to appointment records."
            aria-describedby="audit-focus-help" maxLength={1000}
            value={focus} onChange={(e) => setFocus(e.target.value)} />
        </Field>
      ) : (
        <Field label="Only these folders (optional)" help="Enter folder paths separated by commas. Leave blank to audit everything.">
          <input id="audit-scope" className="input" value={focus} onChange={(e) => setFocus(e.target.value)} />
        </Field>
      )}
      {local && <p id="audit-focus-help" className="muted" style={{ margin: 0 }}>Describe the services, controls, or gaps you want reviewed. Your instructions stay with the audit.</p>}
    </Modal>
  )
}
