import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, fmt, getProjectSlug, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, Empty, ErrorNote, Field, Loading, Modal, Pager, SampleTag, Sev, Status, useAction } from '../components/ui'
import Explain from '../components/Explain'

const WAITING_PAGE = 10 // fix cards per page
const DECIDED_PAGE = 20 // decided rows per page

export default function Approvals() {
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()
  const waitingAt = Math.max(1, Number(params.get('waiting')) || 1)
  const decidedAt = Math.max(1, Number(params.get('decided')) || 1)
  const turn = (name) => (n) => setParams((was) => { const next = new URLSearchParams(was); if (n > 1) next.set(name, n); else next.delete(name); return next })
  const waitingList = useApi(`/api/remediations?status=pending&page=${waitingAt}&size=${WAITING_PAGE}`)
  const decidedList = useApi(`/api/remediations?status=decided&page=${decidedAt}&size=${DECIDED_PAGE}`)
  const reload = () => { waitingList.reload(); decidedList.reload() }
  const error = waitingList.error || decidedList.error
  const loading = (waitingList.loading && !waitingList.data) || (decidedList.loading && !decidedList.data)
  const overview = useApi('/api/overview')
  const decisions = useDecisions(reload)
  const { busy, markDone, approve, rejectModal } = decisions
  const decider = allowed(user, 'approve')
  const operator = allowed(user, 'triage')

  if (loading || (overview.loading && !overview.data && !overview.error)) return <Loading rows={5} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const pending = waitingList.data.items
  const decided = decidedList.data.items
  const queue = overview.data?.decisions || []
  const extras = queue.filter((d) => d.kind !== 'fix')
  const waiting = extras.length + waitingList.data.total

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div><h2>{waiting ? `${waiting} ${waiting === 1 ? 'decision waits' : 'decisions wait'} for you` : 'All caught up'}</h2>
          <p>WhiteHats never changes your systems on its own. You decide.</p></div>
      </div>
      {!decider && <div className="banner info"><div className="row between"><span>Only Approvers in this project can approve.</span><button className="btn" type="button" disabled>Approve</button></div></div>}
      {extras.map((d) => (
        <Card key={`${d.kind}-${d.id}`}>
          <div className="card-body stack">
            <div className="row between"><Sev s={d.severity} /><span className="pill info">{d.kind === 'factory' ? 'Proven fixes' : 'Blind spot'}</span></div>
            <h3 style={{ fontSize: 17 }}>{d.title}</h3>
            <p className="muted">{d.detail}</p>
            <Link to={d.link} className="btn small">Review</Link>
          </div>
        </Card>
      ))}
      {waitingList.data.total === 0 && extras.length === 0 ? <Card><Empty title="No fixes waiting">New fixes appear here as the WhiteHats propose them.</Empty></Card> : (
        <div className="grid g2">
          {pending.map((r) => (
            <Card key={r.id}>
              <div className="card-body stack">
                <div className="row between"><Sev s={r.risk} /><span className="faint mono">FIX-{r.id}</span></div>
                <h3 style={{ fontSize: 17 }} className="row wrap"><Link to={`/approvals/${r.id}`} className="fix-title">{r.title}</Link><SampleTag origin={r.origin} /></h3>
                <p>{r.action}</p>
                {r.why && <p className="muted" style={{ fontSize: 13 }}><b>Why:</b> {r.why}</p>}
                <ImpactBanner impact={r.impact_banner} />
                <div className="row wrap" style={{ fontSize: 12.5 }}>
                  <span className="muted">Target</span><b>{r.target}</b><span className="muted">· Carried out by</span><b>{r.destination}</b>
                  {r.incident_id && <Link to={`/incidents/${r.incident_id}`}>· View incident</Link>}
                </div>
                <Decide r={r} decisions={decisions} details />
              </div>
            </Card>
          ))}
        </div>
      )}
      <Pager page={waitingList.data.page} size={waitingList.data.size} total={waitingList.data.total} onPage={turn('waiting')} label="fixes waiting" />
      <Card title="Decided">
        {decided.length === 0 ? <Empty title="No decisions yet" /> : (
          <div className="tbl-wrap tbl-stack"><table className="t"><thead><tr><th>Fix</th><th>Outcome</th><th>By</th><th>When</th><th>Result</th></tr></thead>
            <tbody>{decided.map((r) => (
              <tr key={r.id}>
                <td data-label="Fix"><span className="row wrap"><Link to={`/approvals/${r.id}`} className="fix-title"><b>{r.title}</b></Link><SampleTag origin={r.origin} /></span><div className="muted" style={{ fontSize: 12 }}>{r.target} · {r.destination}</div></td>
                <td data-label="Outcome"><Status s={r.status} /></td>
                <td data-label="By">{r.decided_by}</td>
                <td data-label="When" className="num"><time dateTime={r.decided_at}>{fmt.time(r.decided_at)}</time></td>
                <td data-label="Result">
                  <ResultText text={r.result} />
                  {r.action && !(r.result || '').includes(r.action) && <div style={{ marginTop: 4 }}>{r.action}</div>}
                  {r.done_by && <div className="muted" style={{ fontSize: 12 }}>Marked done by {r.done_by}{r.done_at ? ` · ${fmt.time(r.done_at)}` : ''}</div>}
                  {!r.done_by && r.done_at && <div className="muted" style={{ fontSize: 12 }}>Outcome from Fix Factory · {fmt.time(r.done_at)}</div>}
                  {r.status === 'failed' && r.destination === 'Fix Factory' && decider && (
                    <button className="btn small" style={{ marginTop: 6 }} disabled={busy === `a${r.id}`} onClick={() => approve(r)}>
                      {busy === `a${r.id}` ? <span className="spin" /> : 'Try again'}
                    </button>
                  )}
                  {r.status === 'dispatched' && r.closes_itself && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Closes itself when WhiteHats reads the change in AWS</div>}
                  {r.status === 'dispatched' && r.manual && !r.closes_itself && operator && (
                    <button className="btn small" style={{ marginTop: 6 }} disabled={busy === `d${r.id}`} onClick={() => markDone(r)}>
                      {busy === `d${r.id}` ? <span className="spin" /> : 'Mark as done'}
                    </button>
                  )}
                </td>
              </tr>
            ))}</tbody></table></div>
        )}
        <div className="card-foot"><Pager page={decidedList.data.page} size={decidedList.data.size} total={decidedList.data.total} onPage={turn('decided')} label="decided fixes" /></div>
      </Card>
      {rejectModal}
    </div>
  )
}

function ImpactBanner({ impact }) {
  const text = impact?.text || 'Impact not simulated yet'
  const runId = impact?.run_id
  return (
    <div className={`banner ${runId ? 'good' : 'info'}`} style={{ fontSize: 12.5 }}>
      <div>{runId ? text : <Explain term="impact_not_simulated"><span>{text}</span></Explain>}{runId && <> · <Link to="/fix-factory">View run {runId}</Link></>}</div>
    </div>
  )
}

// A result can list pull request addresses, one per line; those become links. Everything else stays text.
function ResultText({ text }) {
  return (
    <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
      {(text || '').split('\n').map((line, i) => {
        const at = line.lastIndexOf(' https://')
        const url = at >= 0 ? line.slice(at + 1) : ''
        return (
          <div key={i}>{url && !url.includes(' ')
            ? <>{line.slice(0, at + 1)}<a href={url} target="_blank" rel="noreferrer">{url}</a></>
            : line}</div>
        )
      })}
    </div>
  )
}

// Approve, reject and mark done, shared by the list and a fix's own page so both decide the same way.
function useDecisions(reload) {
  const navigate = useNavigate()
  const { org } = useAuth()
  const [busy, run] = useAction()
  const [rejecting, setRejecting] = useState(null)
  const [reason, setReason] = useState('')
  const approve = (r) => run(`a${r.id}`, () => api(`/api/remediations/${r.id}/approve`, { method: 'POST' }),
    (out) => {
      if (out.status === 'pending') return 'First approval recorded. A second Approver must decide.'
      if (out.factory_run_id) return 'Sent to Fix Factory to be proven'
      return out.closes_itself ? 'Taken on. WhiteHats closes it when AWS shows the change' : `Manual: carry this out in ${out.destination}`
    }).then(out => {
      if (org?.local_workspace && out?.factory_run_id) {
        navigate(`/p/${getProjectSlug()}/fix-factory?run=${encodeURIComponent(out.factory_run_id)}`)
      } else {
        reload()
      }
    })
  const markDone = (r) => run(`d${r.id}`, () => api(`/api/remediations/${r.id}/done`, { method: 'POST' }),
    (out) => (out.status === 'verifying' ? 'Marked done. WhiteHats is checking it' : 'Marked as done')).then(reload)
  const byHand = (r) => run(`h${r.id}`, () => api(`/api/remediations/${r.id}/by-hand`, { method: 'POST', body: { note: '' } }),
    'Yours to fix. Mark it done when it is, and WhiteHats checks it').then(reload)
  const [risking, setRisking] = useState(null)
  const [until, setUntil] = useState('')
  const acceptRisk = () => run('risk', () => api(`/api/remediations/${risking.id}/accept-risk`, { method: 'POST', body: { reason, until } }),
    `Risk accepted until ${until}`).then((out) => { if (out) { setRisking(null); setReason(''); setUntil(''); reload() } })
  const riskModal = risking && (
    <Modal title={`Accept the risk: ${risking.title}`} onClose={() => setRisking(null)}
      footer={<><button className="btn" onClick={() => setRisking(null)}>Cancel</button>
        <button className="btn primary" disabled={busy === 'risk' || reason.trim().length < 8 || !until} onClick={acceptRisk}>Accept the risk</button></>}>
      <p className="muted" style={{ marginTop: 0 }}>The finding closes and alerts no one until the date. Then it opens again and asks for a decision.</p>
      <Field label="Why is it acceptable for now?" help="A short sentence; it goes on the record with your name.">
        <textarea id="risk-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: the service is internal only until the March release" />
      </Field>
      <Field label="Until" help="WhiteHats asks again on this date.">
        <input id="risk-until" className="input" type="date" value={until} min={new Date(Date.now() + 86400000).toISOString().slice(0, 10)} onChange={(e) => setUntil(e.target.value)} style={{ maxWidth: '14rem' }} />
      </Field>
    </Modal>
  )
  const reject = () => run('reject', () => api(`/api/remediations/${rejecting.id}/reject`, { method: 'POST', body: { reason } }), 'Rejected')
    .then(() => { setRejecting(null); setReason(''); reload() })
  const rejectModal = rejecting && (
    <Modal title={`Reject: ${rejecting.title}`} onClose={() => setRejecting(null)}
      footer={<><button className="btn" onClick={() => setRejecting(null)}>Cancel</button>
        <button className="btn danger" disabled={busy === 'reject'} onClick={reject}>Reject fix</button></>}>
      <Field label="Why are you rejecting it?" help="This goes on the incident record so the team knows what to do instead.">
        <textarea id="reject-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: needs a maintenance window" />
      </Field>
    </Modal>
  )
  return { busy, approve, markDone, byHand, ask: setRejecting, askRisk: (r) => { setReason(''); setRisking(r) }, rejectModal: <>{rejectModal}{riskModal}</> }
}

function Decide({ r, decisions, details = false }) {
  const { user } = useAuth()
  const { busy, approve, ask, byHand, askRisk } = decisions
  const decider = allowed(user, 'approve')
  return (
    <>
      {r.first_approved_by && (
        <p className="banner info" style={{ fontSize: 13 }}>
          {r.first_approved_by_id === user?.id
            ? 'You gave the first approval. This project needs a second, different Approver for a critical fix.'
            : `First approval by ${r.first_approved_by}. This project needs a second, different Approver for a critical fix.`}
        </p>
      )}
      {(decider || details) && (
        <div className="row wrap" style={{ marginTop: 6 }}>
          {decider && <>
            <Explain term={r.destination === 'Fix Factory' ? 'apply_fix' : r.closes_itself ? 'take_it_on' : 'approve'}>
              <button className="btn primary" disabled={busy === `a${r.id}` || r.first_approved_by_id === user?.id} onClick={() => approve(r)}>
                {busy === `a${r.id}` ? <span className="spin" /> : r.destination === 'Fix Factory' ? 'Apply Fix' : r.closes_itself ? 'Take it on' : 'Approve'}
              </button>
            </Explain>
            {r.status === 'pending' && r.signal_id && <button className="btn" disabled={busy === `h${r.id}`} onClick={() => byHand(r)}>{busy === `h${r.id}` ? <span className="spin" /> : "We'll fix it ourselves"}</button>}
            {r.status === 'pending' && r.signal_id && <button className="btn" onClick={() => askRisk(r)}>Accept the risk</button>}
            <button className="btn" onClick={() => ask(r)}>Reject</button>
          </>}
          {details && <Link to={`/approvals/${r.id}`} className="btn ghost">See details<span aria-hidden="true"> →</span></Link>}
        </div>
      )}
    </>
  )
}

const MODEL_WORDS = {
  confirmed: 'The AI model confirmed it',
  rejected: 'The AI model judged it a false alarm',
  uncertain: 'The AI model was not sure',
  'no answer': 'No AI model answered',
  'not asked': 'The AI model was not asked',
}

export function FixDetail() {
  const { id } = useParams()
  const { data, error, loading, reload } = useApi(`/api/remediations/${id}`)
  const decisions = useDecisions(reload)
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const r = data
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Link to="/approvals" className="muted">← All approvals</Link>
      <div className="page-head">
        <div>
          <div className="row wrap"><span className="mono muted">FIX-{r.id}</span><Sev s={r.risk} /><Status s={r.status} /><SampleTag origin={r.origin} /></div>
          <h2 style={{ marginTop: 6 }}>{r.title}</h2>
          {r.why && <p><b>Why:</b> {r.why}</p>}
          <div className="row wrap fix-facts">
            <span className="muted">Target</span><b>{r.target}</b><span className="muted">· Carried out by</span><b>{r.destination}</b>
            {r.incident_id && <Link to={`/incidents/${r.incident_id}`}>· View incident</Link>}
          </div>
        </div>
      </div>
      <Evidence evidence={r.evidence} />
      <Change change={r.change} impact={r.impact_banner} />
      <Touches touches={r.touches} />
      {r.status === 'pending'
        ? <Card><div className="card-body stack"><Decide r={r} decisions={decisions} /></div></Card>
        : <Card title="Decision"><div className="card-body"><p><Status s={r.status} /> {r.decided_by && <>by <b>{r.decided_by}</b></>} {r.decided_at && <time dateTime={r.decided_at}>{fmt.time(r.decided_at)}</time>}</p>{r.result && <ResultText text={r.result} />}</div></Card>}
      {decisions.rejectModal}
    </div>
  )
}

function Evidence({ evidence }) {
  if (evidence.kind === 'finding') {
    const f = evidence.finding
    return (
      <Card title="What was found" hint={`Found by ${f.found_by}`}>
        <div className="card-body stack">
          <div className="row wrap"><Sev s={f.severity} /><b>{f.title}</b><Status s={f.status} /></div>
          <p className="mono fix-where">{f.path}:{f.line} on {f.branch || 'the default branch'} in {f.repo}{f.commit && <> · commit {f.commit}</>}
            {f.check_url && <> · <a href={f.check_url} target="_blank" rel="noreferrer">WhiteHats check on GitHub</a></>}</p>
          <p>{f.explanation}</p>
          <dl className="fix-dl">
            <dt>Scanner</dt><dd>{f.engine || 'not recorded'} · rule <span className="mono">{f.rule}</span>{f.cwe?.length > 0 && <> · {f.cwe.join(', ')}</>}</dd>
            <dt>AI model</dt><dd>{MODEL_WORDS[f.model.status] || f.model.status}{f.model.model && <> ({f.model.model})</>}{f.model.text && f.model.status !== 'not asked' && f.model.status !== 'no answer' && <>: {f.model.text}</>}</dd>
          </dl>
          <CodeLines code={f.code} path={f.path} />
        </div>
      </Card>
    )
  }
  if (evidence.kind === 'setting') {
    const f = evidence.finding
    return (
      <Card title="What was found" hint="Found in the live AWS settings the map reads">
        <div className="card-body stack">
          <div className="row wrap"><Sev s={f.severity} /><b>{f.title}</b><Status s={f.status} /></div>
          <p className="mono fix-where">{f.resource}</p>
          <p>{f.detail}</p>
          <dl className="fix-dl">
            <dt>Check</dt><dd>WhiteHats setup check · rule <span className="mono">{f.rule}</span></dd>
            {f.status === 'closed' && <><dt>Closed</dt><dd>{f.closed_reason}</dd></>}
          </dl>
        </div>
      </Card>
    )
  }
  if (evidence.kind === 'incident') {
    return (
      <Card title="What was found" hint={evidence.incident ? `From incident ${evidence.incident.ref}` : ''}>
        <div className="card-body stack">
          {evidence.signals.length === 0 && <p className="muted">The incident has no signals recorded.</p>}
          {evidence.signals.map((s, i) => (
            <div key={i} className="fix-signal">
              <div className="row wrap"><Sev s={s.severity} /><b>{s.title}</b></div>
              <p className="muted" style={{ fontSize: 13 }}>{s.source}{s.where && <> · {s.where}</>} · <time dateTime={s.at}>{fmt.time(s.at)}</time></p>
              {s.detail && <p style={{ fontSize: 13.5 }}>{s.detail}</p>}
            </div>
          ))}
        </div>
      </Card>
    )
  }
  return <Card title="What was found"><div className="card-body"><p className="muted">This fix was not proposed from a finding or an incident, so there is no evidence to show.</p></div></Card>
}

function CodeLines({ code, path }) {
  if (!code) return null
  if (code.problem) return <p className="muted" style={{ fontSize: 13 }}>Code not shown: {code.problem}</p>
  return (
    <figure className="fix-code" aria-label={`${path}, lines ${code.lines[0]?.number} to ${code.lines.at(-1)?.number}`}>
      <figcaption className="muted">{path} as checked{code.ref && <> at <span className="mono">{code.ref}</span></>}</figcaption>
      <pre tabIndex={0}>{code.lines.map((l) => (
        <span key={l.number} className={l.flagged ? 'flagged' : undefined}><span className="ln" aria-hidden="true">{l.number}</span>{l.text || ' '}{'\n'}</span>
      ))}</pre>
    </figure>
  )
}

function Change({ change, impact }) {
  if (change.kind === 'manual') {
    return (
      <Card title="What will change" hint={change.destination === 'A person' ? 'Fix Factory cannot make this change, so a person does' : `A person carries this out in ${change.destination}`}>
        <div className="card-body">
          {change.steps.length ? <ol className="fix-steps">{change.steps.map((s, i) => <li key={i}>{s}</li>)}</ol> : <p className="muted">No steps were written for this fix.</p>}
        </div>
      </Card>
    )
  }
  if (change.kind === 'planned') {
    return (
      <Card title="What will change" hint="Fix Factory has not worked on this yet">
        <div className="card-body stack">
          <p>{change.summary}</p>
          <p className="muted" style={{ fontSize: 13 }}>
            {change.ran
              ? `Fix Factory run ${change.run_id} cannot be read right now, so its change is not shown.`
              : 'Apply Fix sends this to Fix Factory. It writes a check that fails on today\'s code, then a fix that makes it pass, and opens a pull request. Nothing is merged until a person merges it.'}
          </p>
          <ImpactBanner impact={impact} />
        </div>
      </Card>
    )
  }
  return (
    <Card title="What will change" hint={`Fix Factory run ${change.run_id} · ${change.status}`}>
      <div className="card-body stack">
        {change.summary && <p>{change.summary}</p>}
        {change.verification.length > 0 && (
          <ul className="fix-checks">{change.verification.map((v, i) => (
            <li key={i}><b>{v.check}</b>{v.before && <>: before the fix {v.before}</>}{v.after && <>, after it {v.after}</>}</li>
          ))}</ul>
        )}
        {change.pull_requests.length > 0 && (
          <p className="row wrap">Pull requests: {change.pull_requests.map((p) => <a key={p.number} href={p.url} target="_blank" rel="noreferrer">#{p.number}{p.part && ` ${p.part}`}</a>)}</p>
        )}
        {change.diff ? <pre className="code" tabIndex={0} aria-label="The change">{change.diff}</pre> : <p className="muted">The run recorded no change.</p>}
        {change.diff_cut && <p className="muted" style={{ fontSize: 13 }}>The change is longer than this page shows; the pull requests have all of it.</p>}
      </div>
    </Card>
  )
}

function Touches({ touches }) {
  return (
    <Card title="What it touches" hint="From the digital twin">
      <div className="card-body stack">
        {!touches.on_twin ? <p className="muted">{touches.why}</p> : <>
          <NodeList title="Where the fix lands" nodes={touches.where} />
          <NodeList title="Connected to it" nodes={touches.near} empty="Nothing else on the twin connects to it." />
        </>}
      </div>
    </Card>
  )
}

function NodeList({ title, nodes, empty }) {
  return (
    <div>
      <h4 className="fix-sub">{title}</h4>
      {nodes.length === 0 ? <p className="muted">{empty}</p> : (
        <ul className="fix-nodes">{nodes.map((n) => (
          <li key={n.id}><b>{n.name}</b> <span className="muted">{n.type}</span>
            {n.criticality && <span className="pill neutral">{n.criticality}</span>}
            {n.data && <span className="pill warn">{n.data} data</span>}
            <span className="muted"> · {n.why}</span></li>
        ))}</ul>
      )}
    </div>
  )
}
