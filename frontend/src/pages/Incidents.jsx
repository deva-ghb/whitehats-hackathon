import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmt, getProjectSlug, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, Empty, ErrorNote, Field, Loading, Modal, SampleTag, Select, Sev, Status, Tabs, useAction, useToast } from '../components/ui'
import { INCIDENT_STATUSES, NEXT_STATUS } from '../lib/choices'
import { followRow } from '../lib/rows'
import Prose from '../components/Prose'
import FalsePositive from '../components/FalsePositive'

const SEVERITIES = [
  { value: 'critical', label: 'Critical', description: 'A serious break that needs someone now.' },
  { value: 'high', label: 'High', description: 'Important, and it should be worked soon.' },
  { value: 'medium', label: 'Medium', description: 'Real, and it can wait its turn.' },
  { value: 'low', label: 'Low', description: 'Worth recording, not urgent.' },
]
const OPEN_BY_HAND = 'Incidents are opened by hand for now. Automatic correlation is coming.'

export default function Incidents() {
  const nav = useNavigate()
  const { user } = useAuth()
  const operator = allowed(user, 'triage')
  const [tab, setTab] = useState('open')
  const [opening, setOpening] = useState(false)
  const { data, error, loading, reload } = useApi(`/api/incidents${tab === 'open' ? '?status=open' : ''}`, { deps: [tab] })
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div><h2>Incidents</h2><p>Each incident brings together what the WhiteHats saw, what it means for the business, and what has been done.</p></div>
        {operator && <button className="btn primary" onClick={() => setOpening(true)}>Open an incident</button>}
      </div>
      <Tabs tabs={[['open', 'Open'], ['all', 'All']]} value={tab} onChange={setTab} />
      {error && <ErrorNote error={error} retry={reload} />}
      <Card>
        {loading && !data ? <Loading /> : !data?.length ? (
          <Empty title="No incidents" action={operator && <button className="btn primary" onClick={() => setOpening(true)}>Open an incident</button>}>{OPEN_BY_HAND}</Empty>
        ) : (
          <div className="tbl-wrap tbl-stack"><table className="t"><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Business impact</th><th>Owner</th><th>Opened</th></tr></thead>
            <tbody>{data.map((i) => {
              const href = `/incidents/${i.id}`
              return (
              <tr key={i.id} className="click" onClick={(e) => followRow(e, href, nav)} onAuxClick={(e) => followRow(e, href, nav)}>
                <td data-label="Incident"><Link className="row-link" to={href}><span className="mono muted">{i.ref}</span><div className="row wrap"><b>{i.title}</b><SampleTag origin={i.origin} /></div><div className="muted" style={{ fontSize: 12 }}>{i.signals} signals</div></Link></td>
                <td data-label="Severity"><Sev s={i.severity} /></td><td data-label="Status"><Status s={i.status} /></td>
                <td data-label="Business impact" style={{ maxWidth: 360 }} className="muted">{i.impact || '—'}</td><td data-label="Owner">{i.owner || <span className="muted">Unassigned</span>}</td>
                <td data-label="Opened" className="num">{fmt.ago(i.created_at)}</td>
              </tr>
              )
            })}</tbody></table></div>
        )}
      </Card>
      {opening && <OpenIncident onClose={() => setOpening(false)} onOpened={(row) => nav(`/incidents/${row.id}`)} />}
    </div>
  )
}

function OpenIncident({ onClose, onOpened }) {
  const { data, error, loading, reload } = useApi('/api/signals?unlinked=1&days=30')
  const [title, setTitle] = useState('')
  const [severity, setSeverity] = useState('high')
  const [chosen, setChosen] = useState([])
  const [busy, run] = useAction()
  const toggle = (id) => setChosen((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  const save = () => run('open', () => api('/api/incidents', {
    method: 'POST', body: { title, severity, signal_ids: chosen },
  }), 'Incident opened').then((row) => { if (row) onOpened(row) })
  const ready = title.trim().length >= 8 && chosen.length > 0
  return (
    <Modal title="Open an incident" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy === 'open' || !ready} onClick={save}>{busy === 'open' ? <span className="spin" /> : 'Open an incident'}</button></>}>
      <div className="stack">
        <p className="muted" style={{ fontSize: 13 }}>{OPEN_BY_HAND}</p>
        {error && <ErrorNote error={error} retry={reload} />}
        <Field label="Title" help="A short sentence. At least 8 characters.">
          <input id="incident-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Severity">
          <Select id="incident-severity" value={severity} onChange={setSeverity} options={SEVERITIES} />
        </Field>
        <Field label="Signals" help="Choose the findings that belong together. A signal already on an incident is not listed.">
          {loading && !data ? <Loading rows={2} /> : !data?.length ? <p>Every signal is already on an incident.</p> : (
            <div className="stack" style={{ maxHeight: 240, overflow: 'auto', gap: 8 }}>
              {data.map((s) => (
                <label key={s.id} className="row" style={{ alignItems: 'flex-start' }}>
                  <input type="checkbox" checked={chosen.includes(s.id)} onChange={() => toggle(s.id)} style={{ marginTop: 3 }} />
                  <span style={{ minWidth: 0 }}><b>{s.title}</b><span className="muted" style={{ fontSize: 12 }}> {s.agent || s.source}</span></span>
                </label>
              ))}
            </div>
          )}
        </Field>
      </div>
    </Modal>
  )
}

export function IncidentDetail() {
  const { id } = useParams()
  const { user } = useAuth()
  const toast = useToast()
  const { data, error, loading, reload, setData } = useApi(`/api/incidents/${id}`, { poll: 10000 })
  const [busy, run] = useAction()
  const [note, setNote] = useState('')
  const [statusOpen, setStatusOpen] = useState(false)
  const [nextStatus, setNextStatus] = useState('')
  const [statusNote, setStatusNote] = useState('')
  const [falseOpen, setFalseOpen] = useState(false)
  const [falseReason, setFalseReason] = useState('')
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const operator = allowed(user, 'triage')
  const patch = (body, msg) => run('patch', () => api(`/api/incidents/${id}`, { method: 'PATCH', body }), msg).then((d) => d && setData(d))
  const unverified = data.remediations.filter((r) => r.status === 'pending' || r.status === 'dispatched')
  const pendingFixes = unverified.filter((r) => r.status === 'pending')
  const failedFixes = data.remediations.filter((r) => r.status === 'failed')
  const statusOptions = (NEXT_STATUS[data.status] || []).map((value) => {
    const meta = INCIDENT_STATUSES.find((s) => s.value === value)
    const blocked = value === 'verifying' && unverified.length > 0
    const reason = pendingFixes.length
      ? `Still waiting on a decision: ${pendingFixes.map((r) => r.title).join('; ')}.`
      : `Not verified yet: ${unverified.map((r) => r.title).join('; ')}.`
    return { ...meta, disabled: blocked, disabledReason: blocked ? reason : meta.description }
  })
  const chosen = statusOptions.find((opt) => opt.value === nextStatus)
  const saveStatus = () => run('status', () => api(`/api/incidents/${id}`, { method: 'PATCH', body: { status: nextStatus, note: statusNote } }), null)
    .then((d) => {
      if (!d) return
      setData(d)
      setStatusOpen(false)
      const label = INCIDENT_STATUSES.find((s) => s.value === d.status)?.label || d.status
      toast(`Status is now ${label}`, 'info', {
        label: 'Undo',
        ms: 10000, // same 10 seconds as UNDO_SECONDS on the server
        onClick: () => run('undo', () => api(`/api/incidents/${id}`, { method: 'PATCH', body: { undo: true } }), 'Status restored').then((back) => back && setData(back)),
      })
    })
  const approver = allowed(user, 'approve')
  const closed = data.status === 'resolved' || data.status === 'false_positive'
  const sayFalse = () => run('false', () => api(`/api/incidents/${id}/false-positive`, { method: 'POST', body: { reason: falseReason } }),
    'Closed as a false positive. WhiteHats will not raise it again').then((d) => { if (d) { setData(d); setFalseOpen(false); setFalseReason('') } })
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Link to="/incidents" className="muted">← All incidents</Link>
      <div className="page-head">
        <div>
          <div className="row"><span className="mono muted">{data.ref}</span><Sev s={data.severity} /><Status s={data.status} /><SampleTag origin={data.origin} /></div>
          <h2 style={{ marginTop: 6 }}>{data.title}</h2>
          <Prose text={data.summary} className="incident-summary" />
        </div>
        {operator && (
          <div className="row">
            {statusOptions.length > 0 && <button className="btn" id="change-status" onClick={() => { setNextStatus((statusOptions.find((opt) => !opt.disabled) || statusOptions[0]).value); setStatusNote(''); setStatusOpen(true) }}>Change status</button>}
            {!data.owner && !closed && <button className="btn" onClick={() => patch({ owner: user.name }, 'You own this incident now')}>Take ownership</button>}
            {approver && !closed && <button className="btn" onClick={() => setFalseOpen(true)}>False positive</button>}
          </div>
        )}
      </div>
      {data.status === 'verifying' && <div className="banner info" role="status"><span className="spin" /> <div>WhiteHats is checking each finding again. It resolves the incident if they are gone, or sends it back to investigating saying what is still there.</div></div>}
      {data.impact && <div className="banner warn"><div><b>Business impact.</b> {data.impact}</div></div>}
      <div className="split">
        <Card title="Timeline" hint={data.owner ? `Owner: ${data.owner}` : 'No owner yet'}>
          <div className="card-body stack" style={{ gap: 0 }}>
            {data.timeline.map((e, i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '110px 14px 1fr', gap: 10, padding: '8px 0' }}>
                <span className="faint num" style={{ fontSize: 12 }}>{fmt.time(e.at)}</span>
                <span className={`dot ${e.kind === 'action' ? 'good' : e.kind === 'signal' ? 'warn' : 'neutral'}`} style={{ marginTop: 6 }} />
                <div><div>{e.text}</div><div className="faint" style={{ fontSize: 12 }}>{e.actor} · {e.kind}</div></div>
              </div>
            ))}
            {operator && (
              <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); if (note.trim()) patch({ note }, 'Note added').then(() => setNote('')) }}>
                <input id="note" className="input" placeholder="Add a note for the team" value={note} onChange={(e) => setNote(e.target.value)} />
                <button className="btn" disabled={busy === 'patch' || !note.trim()}>Add</button>
              </form>
            )}
          </div>
        </Card>
        <div className="stack" style={{ gap: 16 }}>
          <Card title="Automated fixes and approvals" action={<Link to={`/p/${getProjectSlug()}/fix-factory`} className="btn small">Fix Factory</Link>}>
            {data.remediations.length === 0 ? <Empty title="No fixes proposed yet" /> : data.remediations.map((r) => (
              <div key={r.id} className="list-item"><div className="row between"><span className="row wrap"><Link to={`/p/${getProjectSlug()}/approvals/${r.id}`}><b>{r.title}</b></Link></span><Status s={r.status} /></div>
                <div className="muted" style={{ fontSize: 12.5 }}>{r.destination} · {r.impact_banner?.text || 'Impact assessment pending'}</div>
                {r.result && <div style={{ fontSize: 12.5, marginTop: 4 }}>{r.result}</div>}</div>
            ))}
          </Card>
          <Card title="Signals" hint="What each WhiteHat saw">
            {data.signals.map((s) => (
              <div key={s.id} className="list-item"><div className="row between"><div className="row wrap"><Sev s={s.severity} /><b style={{ fontSize: 13 }}>{s.title}</b><SampleTag origin={s.origin} /></div>
                {!closed && s.origin !== 'sample' && <FalsePositive signal={s} onDone={reload} className="btn sm ghost" />}</div>
                <div className="muted" style={{ fontSize: 12 }}>{[s.agent, s.entity, s.mitre && `MITRE ${s.mitre}`, fmt.time(s.at)].filter(Boolean).join(' · ')}</div></div>
            ))}
          </Card>
        </div>
      </div>
      {statusOpen && (
        <Modal title="Change status" onClose={() => setStatusOpen(false)} footer={<><button className="btn" onClick={() => setStatusOpen(false)}>Cancel</button>
          <button className="btn primary" disabled={busy === 'status' || statusNote.trim().length < 8 || !chosen || chosen.disabled} onClick={saveStatus}>Update status</button></>}>
          <div className="stack">
            <Field label="Next status" help="WhiteHats resolves it after checking each finding again. Every proposed fix must be decided first.">
              <Select id="incident-status" value={nextStatus} onChange={setNextStatus} options={statusOptions} />
            </Field>
            {nextStatus === 'verifying' && failedFixes.length > 0 && (
              <div className="banner warn" id="failed-fix-warning" role="status">
                The automated fix failed: {failedFixes.map((r) => r.title).join('; ')}. If you fixed it by hand, WhiteHats checks it all the same. The timeline will record that the automated fix failed.
              </div>
            )}
            <Field label="Note for the timeline" help="A short sentence about why the status is changing.">
              <textarea id="status-note" className="input" value={statusNote} onChange={(e) => setStatusNote(e.target.value)} placeholder="For example: the probing host is isolated" />
            </Field>
          </div>
        </Modal>
      )}
      {falseOpen && (
        <Modal title="Close as a false positive" onClose={() => setFalseOpen(false)} footer={<><button className="btn" onClick={() => setFalseOpen(false)}>Cancel</button>
          <button className="btn danger" disabled={busy === 'false' || falseReason.trim().length < 8} onClick={sayFalse}>Close as a false positive</button></>}>
          <p className="muted" style={{ marginTop: 0 }}>Every finding on this incident closes, and WhiteHats does not raise the same ones again. It learns this as an accepted exception and counts it against the WhiteHat that raised it.</p>
          <Field label="Why is it not a real problem?" help="A short sentence; it goes on the record with your name.">
            <textarea id="false-reason" className="input" value={falseReason} onChange={(e) => setFalseReason(e.target.value)} placeholder="For example: the key is a published test key" />
          </Field>
        </Modal>
      )}
    </div>
  )
}
