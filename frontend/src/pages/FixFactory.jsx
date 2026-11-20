import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, Empty, ErrorNote, Loading, Sev, Stat, Tabs, useAction } from '../components/ui'

const LABEL = {
  verified: ['Proven fix', 'good'], review_flagged: ['Proven · reviewer flagged', 'warn'], failed: ['Not fixed · to a person', 'crit'],
  ci_failed: ['CI failed · to a person', 'crit'],
  declined: ['Declined · to a person', 'warn'], not_reproduced: ['Nothing to fix', 'neutral'], error: ['Error', 'crit'],
  running: ['Running', 'info'], queued: ['Queued', 'neutral'],
}
// A pull request fix waits for a person to merge it on GitHub; any other proven fix waits for approval here.
const runLabel = (r, merges = false) => r.approval === 'approved' ? ['Merged', 'good']
  : r.approval === 'pending' ? [merges ? 'Awaiting merge' : 'Awaiting approval', 'warn']
  : r.approval === 'rejected' ? ['Rejected', 'neutral'] : r.approval === 'withdrawn' ? ['Withdrawn', 'neutral'] : LABEL[r.status] || [r.status, 'neutral']

const STEPS = [['reproduce', 'Reproduce'], ['triage', 'Triage'], ['fix', 'Fix'], ['verify', 'Prove'], ['review', 'Review'], ['approve', 'Approve']]
const PR_STEPS = [...STEPS.slice(0, 5), ['deliver', 'Pull requests'], ['approve', 'Merged']]
const CI = { passed: ['CI passed', 'good'], failed: ['CI failed', 'crit'], unfinished: ['CI did not finish', 'warn'] }
const REPLAYED = 'Recorded run, replayed'

export default function FixFactory() {
  const { user, org } = useAuth()
  const local = org?.local_workspace
  const [params] = useSearchParams()
  const requested = params.get("run")
  const status = useApi('/api/factory/status', { poll: 10000 })
  const connected = status.data?.connected
  const inbox = useApi(connected ? '/api/factory/inbox' : null, { poll: 4000, deps: [connected] })
  const runs = useApi(connected ? '/api/factory/runs' : null, { poll: 4000, deps: [connected] })
  const [mode, setMode] = useState('live')
  const [selected, setSelected] = useState(null)
  const [busy, run] = useAction()

  const newest = local ? runs.data?.runs[0]?.id : status.data?.own_runs_only ? (inbox.data || []).map((i) => i.latest_run).filter(Boolean)
    .sort((a, b) => String(b.started_at || b.id).localeCompare(String(a.started_at || a.id)))[0]?.id : null
  const requestedRun = runs.data?.runs.some(row => row.id === requested) ? requested : null
  useEffect(() => { setSelected(requestedRun) }, [requestedRun])
  useEffect(() => { if (newest && !selected) setSelected(newest) }, [newest, selected])

  if (status.loading && !status.data) return <Loading />
  if (status.data?.elsewhere) {
    return <Card><Empty title="Fix Factory is in the Main project">{status.data.message}</Empty></Card>
  }
  if (!connected) {
    return (
      <Card><div className="card-body stack">
        <h2 style={{ fontSize: '1.17em' }}>Fix Factory engine is not connected</h2>
        <p className="muted">{status.data?.message}</p>
        <pre className="code">{'cd factory\nexport WHITEHATS_DISPATCH_TOKEN=<same value as the platform server>\nuv run factory serve --port 8787'}</pre>
        <p className="muted" style={{ fontSize: 12.5 }}>The platform's start script does this for you: <span className="mono">platform/start.sh</span>.</p>
      </div></Card>
    )
  }
  const s = runs.data?.summary || {}
  const decider = allowed(user, 'approve')
  // Outside Main the page shows the runs this project's fixes started; fixes are sent from Approvals, not here.
  const own = Boolean(status.data?.own_runs_only)
  const send = (item) => run(`s${item.id}`, () => api('/api/factory/runs', { method: 'POST', body: { intent: item.id, mode } }), 'Sent to the factory').then((o) => { if (o) setSelected(o.run); inbox.reload(); runs.reload() })

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div><h2>Findings in, proven fixes out</h2><p>The factory shows the weakness is real, writes the fix in a sealed copy, proves it with the original check, tests and a scanner, gets an independent review, then waits for you.</p></div>
        {!local && !own && <div className="row"><Tabs tabs={[['live', 'Live agents'], ['replay', 'Replay (no cost)']]} value={mode} onChange={setMode} /></div>}
      </div>
      <div className="grid g4">
        <Stat label="Proven fixes" value={s.verified ?? 0} note={`of ${s.runs ?? 0} runs`} />
        <Stat label={local ? "Approved revisions" : "Approved and merged"} value={s.approved ?? 0} note="every one by a person" />
        <Stat label="Average time to a proven fix" value={s.avg_seconds ? Math.round(s.avg_seconds) : '—'} unit={s.avg_seconds ? 's' : ''} />
        <Stat label="Cost per proven fix" value={s.cost_per_fix ? `$${Number(s.cost_per_fix).toFixed(2)}` : '—'} note="AI spend, live runs" />
      </div>
      <div className="split">
        <Card title={local ? 'Fix runs' : own ? "This project's fixes" : 'Inbox from the swarm'} hint={own && !local ? status.data.message : undefined}>
          {local && !runs.data && <Loading />}
          {local && runs.data?.runs.length === 0 && <Empty title="No fixes sent yet">Send a fix from Approvals to start a run.</Empty>}
          {local && runs.data?.runs.map(row => {
            const [label, tone] = runLabel(row)
            return <div className="list-item" key={row.id}>
              <button className="btn factory-run-select" type="button" aria-pressed={selected === row.id} onClick={() => setSelected(row.id)}>
                <b>{row.title}</b><span className={`pill ${tone}`}>{label}</span>
              </button>
            </div>
          })}
          {!local && own && inbox.data?.length === 0 && <Empty title="No fixes sent yet">Apply Fix on Approvals sends a fix here.</Empty>}
          {!local && inbox.error && <div className="card-body"><ErrorNote error={inbox.error} /></div>}
          {!local && (!inbox.data ? <Loading /> : inbox.data.map((i) => {
            const r = i.latest_run
            const [lbl, cls] = r ? runLabel(r, i.delivers === 'pull_request') : ['New finding', 'brand']
            const active = r && ['queued', 'running'].includes(r.status)
            return (
              <div key={i.id} className="list-item" style={{ cursor: r ? 'pointer' : 'default', background: selected === r?.id ? 'var(--surface-2)' : undefined }} onClick={() => r && setSelected(r.id)}>
                <div className="row between"><div className="row"><Sev s={i.severity} /><span className="mono" style={{ color: 'var(--ink-2)' }}>{i.id}</span></div><span className={`pill ${cls}`}>{lbl}</span></div>
                <b style={{ display: 'block', marginTop: 6 }}>{i.title}</b>
                {r?.mode === 'replay' && <div style={{ fontSize: 12.5, marginTop: 4 }}>{REPLAYED}</div>}
                <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>{i.summary.slice(0, 150)}{i.summary.length > 150 ? '…' : ''}</div>
                {!own && <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn small primary" disabled={active || busy === `s${i.id}` || r?.approval === 'pending' || (mode === 'replay' && !i.can_replay)}
                    onClick={(e) => { e.stopPropagation(); send(i) }}>{active ? 'In the factory' : mode === 'replay' ? 'Replay' : r ? 'Run again' : 'Send to factory'}</button>
                </div>}
              </div>
            )
          }))}
        </Card>
        {selected ? <RunPanel key={selected} id={selected} decider={decider} local={local} onChange={() => { inbox.reload(); runs.reload() }} /> :
          <Card><Empty title="Pick a finding">Its run appears here, step by step, as the factory works.</Empty></Card>}
      </div>
    </div>
  )
}

function RunPanel({ id, decider, local, onChange }) {
  const [finished, setFinished] = useState(false)
  // Step by step while it works; after that only merges on GitHub change it, so ask less often.
  const { data, error, reload } = useApi(`/api/factory/runs/${id}`, { poll: finished ? 15000 : 1500 })
  useEffect(() => { setFinished(Boolean(data?.row?.finished_at)) }, [data?.row?.finished_at])
  const [busy, run] = useAction()
  useEffect(() => { if (data?.row?.finished_at) onChange() }, [data?.row?.finished_at]) // eslint-disable-line
  if (error) return <Card pad><ErrorNote error={error} retry={reload} /></Card>
  if (!data) return <Card><Loading rows={5} /></Card>
  const row = data.row
  const seen = new Set(data.events.map((e) => e.stage))
  const merges = Boolean(data.pieces?.length)
  const [lbl, cls] = runLabel(row, merges)
  const done = row.approval === 'rejected' ? 'Rejected' : local ? 'Approved' : 'Merged'
  const decide = (d) => run(d, () => api(`/api/factory/runs/${id}/${d}`, { method: 'POST' }), d === 'approve' ? done : merges ? 'Pull requests closed' : 'Rejected').then(() => { reload(); onChange() })
  return (
    <Card title={row.title} hint={row.mode === 'replay' ? `${REPLAYED} · ${row.id}` : row.id} action={<span className={`pill ${cls}`}>{lbl}</span>}>
      <div className="card-body stack">
        <div className="row wrap" style={{ gap: 6 }}>
          {(merges ? PR_STEPS : STEPS).map(([k, label]) => {
            const done = seen.has(k) || (k === 'approve' && row.approval === 'approved')
            return <span key={k} className={`pill ${done ? 'good' : 'neutral'}`}>{done ? '✓ ' : ''}{label}</span>
          })}
        </div>
        <div role="log" aria-label="What the factory did, step by step" tabIndex={0} style={{ background: 'var(--surface-2)', border: '1px solid var(--line)', borderRadius: 10, padding: 10, maxHeight: 240, overflow: 'auto' }}>
          {row.mode === 'replay' && (
            <div style={{ display: 'grid', gridTemplateColumns: '78px 1fr', gap: 8, fontSize: 12.5, padding: '2px 0' }}>
              <span className="mono" style={{ color: 'var(--ink-2)' }}>replay</span><span>{REPLAYED}</span>
            </div>
          )}
          {data.events.map((e, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '78px 1fr', gap: 8, fontSize: 12.5, padding: '2px 0' }}>
              <span className="mono" style={{ color: 'var(--ink-2)' }}>{e.stage}</span>
              <span style={{ color: e.level === 'ok' ? 'var(--good)' : e.level === 'fail' ? 'var(--crit)' : e.level === 'warn' ? 'var(--warn)' : undefined }}>{e.message}</span>
            </div>
          ))}
          {!row.finished_at && <div className="row" style={{ marginTop: 6 }}><span className="spin" /> working…</div>}
        </div>
        {data.verification?.length > 0 && (
          <table className="t"><thead><tr><th>Check</th><th>Before</th><th>After</th></tr></thead>
            <tbody>{data.verification.map((v) => <tr key={v.check}><td>{v.check}</td><td>{v.before}</td><td><b>{v.after}</b></td></tr>)}</tbody></table>
        )}
        {merges && <PullRequests pieces={data.pieces} rule={data.order_rule} waiting={row.approval === 'pending'} />}
        {data.review?.summary && !merges && <div className={`banner ${data.review.approve ? 'good' : 'warn'}`}><div><b>Independent review:</b> {data.review.summary}</div></div>}
        {data.diff && <pre className="code" tabIndex={0} aria-label="The change">{data.diff}</pre>}
        <div className="row between wrap"><span className="muted" style={{ fontSize: 12.5 }}>{row.mode === 'replay' ? `${REPLAYED}. ` : ''}Score {row.score ?? '—'}/100 · ${Number(row.cost_usd || 0).toFixed(2)} · {row.seconds ? `${row.seconds}s` : fmt.ago(row.started_at)}</span>
          {row.approval === 'pending' && decider && !merges && (
            <div className="row"><button className="btn" onClick={() => decide('reject')} disabled={!!busy}>Reject</button>
              <button className="btn primary" onClick={() => decide('approve')} disabled={!!busy}>{busy === 'approve' ? <span className="spin" /> : local ? 'Approve revision' : 'Approve and merge'}</button></div>
          )}
          {row.approval === 'pending' && decider && merges && (
            <button className="btn" onClick={() => decide('reject')} disabled={!!busy}>{busy === 'reject' ? <span className="spin" /> : 'Reject and close pull requests'}</button>
          )}
        </div>
      </div>
    </Card>
  )
}

function PullRequests({ pieces, rule, waiting }) {
  const title = pieces.length === 1 ? 'Pull request' : 'Pull requests, in merge order'
  return (
    <div className="stack" style={{ gap: 8 }}>
      <h3 id="pull-requests" style={{ fontSize: 14 }}>{title}</h3>
      {pieces.length > 1 && <p className="muted" style={{ fontSize: 12.5 }}>{rule}</p>}
      <ol aria-labelledby="pull-requests" style={{ listStyle: 'none', margin: 0, padding: 0, border: '1px solid var(--line)', borderRadius: 10 }}>
        {pieces.map((p) => {
          const [ci, tone] = CI[p.ci?.state] || ['Not run yet', 'neutral']
          return (
            <li key={p.number} className="list-item" style={{ display: 'grid', gap: 4, padding: '12px 14px', borderTop: p.number === 1 ? 0 : undefined }}>
              <div className="row between wrap" style={{ gap: 8 }}>
                <span className="row wrap" style={{ gap: 8 }}>
                  <span className="num muted">{p.number} of {p.total}</span>
                  <b>{p.part}</b>
                  {p.pull_request
                    ? <a href={p.pull_request.url} target="_blank" rel="noreferrer">#{p.pull_request.number}</a>
                    : <span className="muted">Not opened</span>}
                  {p.merged && <span className="pill good">Merged</span>}
                </span>
                <span className={`pill ${tone}`}>{ci}</span>
              </div>
              <div className="muted" style={{ fontSize: 12.5 }}>{p.kind === 'infrastructure' ? 'Infrastructure' : 'Code'} in <span className="mono">{p.touches}</span>{p.files.length > 0 && <> · changes <span className="mono" style={{ overflowWrap: 'anywhere' }}>{p.files.join(', ')}</span></>}</div>
              {p.review?.summary && <div style={{ fontSize: 12.5 }}><b>Review:</b> {p.review.summary}</div>}
            </li>
          )
        })}
      </ol>
      {waiting && <p className="muted" style={{ fontSize: 12.5 }}>A person merges {pieces.length === 1 ? 'it' : 'them, in this order,'} on GitHub. Fix Factory records each merge; it never merges on its own.</p>}
    </div>
  )
}
