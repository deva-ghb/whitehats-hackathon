import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, Empty, ErrorNote, Field, Loading, Modal, Select, Stat, useAction } from '../components/ui'
import { LOG_WINDOWS } from '../lib/choices'
import { followRow } from '../lib/rows'
import { CollectorTabs } from './Collectors'

// Raw lines come from the account's collectors and belong to no project yet, so the server shows them
// only to the roles that see every project.
const RAW_DATA_NOTE = 'Raw logs come from the account\'s collectors and are not tied to one project yet. Account owners, Account admins, Security leads and Auditors can read them.'

export default function Logs() {
  const { user } = useAuth()
  if (!allowed(user, 'read_account_data')) {
    return (
      <div className="stack" style={{ gap: 16 }}>
        <CollectorTabs on="/collectors/logs" />
        <div className="page-head"><div><h2>From a flood of lines to one page</h2><p>The analyzer groups lines by shape, keeps what changed, and packs the rest into a fixed token budget.</p></div></div>
        <Card><Empty title="Raw logs are kept at account level">{RAW_DATA_NOTE}</Empty></Card>
      </div>
    )
  }
  return <LogsForAccountReaders user={user} />
}

function LogsForAccountReaders({ user }) {
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const shapeId = params.get('shape')
  const [minutes, setMinutes] = useState(60)
  const { data, error, loading, reload } = useApi(`/api/logs/overview?minutes=${minutes}`, { poll: 15000, deps: [minutes] })
  const [budget, setBudget] = useState(1500)
  const [digest, setDigest] = useState(null)
  const [busy, run] = useAction()
  const [shape, setShape] = useState(null)
  const [q, setQ] = useState('')
  const [hits, setHits] = useState(null)
  useEffect(() => {
    if (!shapeId) {
      setShape(null)
      return undefined
    }
    let live = true
    api(`/api/logs/templates/${shapeId}`).then((s) => {
      if (live) setShape(s)
    }).catch(() => {
      if (live) setShape(null)
    })
    return () => { live = false }
  }, [shapeId])
  const closeShape = () => setParams((prev) => {
    const next = new URLSearchParams(prev)
    next.delete('shape')
    return next
  }, { replace: true })
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const d = digest || data.latest_digest
  const funnel = d ? [
    ['Raw log lines', d.lines, 'lines'], ['Raw size for an AI', d.raw_tokens, 'tokens'], ['Distinct shapes', d.templates, 'shapes'],
    ['Notable shapes', d.notable, 'shapes'], ['What the AI reads', d.digest_tokens, 'tokens'],
  ] : []
  const maxLog = Math.log10(Math.max(...funnel.map((f) => f[1] || 1), 10))
  const make = () => run('dg', () => api('/api/logs/digest', { method: 'POST', body: { minutes, budget: Number(budget) } }), 'Digest ready').then((x) => { if (x) { setDigest(x); reload() } })
  return (
    <div className="stack" style={{ gap: 16 }}>
      <CollectorTabs on="/collectors/logs" />
      <div className="page-head">
        <div><h2>From a flood of lines to one page</h2><p>The analyzer never sends raw logs to a model. It groups lines by shape, keeps what changed, and packs the rest into a fixed token budget.</p></div>
        <div className="row" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <Select id="log-window" label="Window" value={minutes} onChange={setMinutes} options={LOG_WINDOWS} />
          <Field label="Token budget" help="Token budget for the digest">
            <input id="log-budget" className="input" type="number" step="100" min="300" value={budget} onChange={(e) => setBudget(e.target.value)} style={{ width: '14rem' }} />
          </Field>
          {allowed(user, 'triage') && <button className="btn primary" disabled={busy === 'dg'} onClick={make}>{busy === 'dg' ? <span className="spin" /> : 'Build digest'}</button>}
        </div>
      </div>
      <div className="grid g4">
        <Stat label="Lines in window" value={fmt.n(data.lines_in_window)} /><Stat label="Shapes in window" value={fmt.n(data.shapes_in_window)} />
        <Stat label="Notable now" value={data.notable.length} tone={data.notable.length ? 'warn' : 'good'} />
        <Stat label="All-time" value={fmt.n(data.all_time.lines)} unit="lines" note={`${fmt.n(data.all_time.templates)} shapes`} />
      </div>
      {!d ? <Card><Empty title="No digest yet" action={<Link to="/collectors" className="btn primary">Deploy a collector</Link>}>Deploy a log collector, then build a digest.</Empty></Card> : (
        <div className="split">
          <Card title="Compression funnel" hint={`Built ${fmt.ago(d.created_at)} · budget ${fmt.n(d.budget_tokens)} tokens`}>
            <div className="card-body funnel">
              {funnel.map(([label, v, unit], i) => (
                <div key={label} className={`funnel-row ${i === funnel.length - 1 ? 'hot' : ''}`}>
                  <span style={{ fontWeight: 700, fontSize: 13 }}>{label}</span>
                  <div className="fb" style={{ width: `${Math.max(2, (Math.log10(Math.max(v, 1)) / maxLog) * 100)}%` }} />
                  <b>{fmt.n(v)} <span className="muted" style={{ fontWeight: 500, fontSize: 12 }}>{unit}</span></b>
                </div>
              ))}
              <div className="banner good" style={{ marginTop: 6 }}>The AI reads {d.raw_tokens ? Math.max(1, Math.round(d.raw_tokens / Math.max(1, d.digest_tokens))) : '—'}× less than the raw logs, and the digest never exceeds its budget however busy the systems get. (Log scale.)</div>
            </div>
          </Card>
          <Card title="What it means" hint={d.model_used ? `Summarised by ${d.model_used}` : ''}>
            <div className="card-body"><p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65 }}>{d.ai_summary || 'Nothing notable.'}</p>
              {d.text && <details style={{ marginTop: 12 }}><summary className="muted" style={{ cursor: 'pointer' }}>Exactly what the AI was given ({fmt.n(d.digest_tokens)} tokens)</summary><pre className="code" style={{ marginTop: 8 }}>{d.text}</pre></details>}</div>
          </Card>
        </div>
      )}
      <Card title="Notable shapes" hint="Ranked by how much a person should care">
        {data.notable.length === 0 ? <Empty title="Nothing notable in this window" /> : (
          <div className="tbl-wrap"><table className="t"><thead><tr><th>Shape</th><th>Why</th><th>Count</th><th>Source</th></tr></thead>
            <tbody>{data.notable.map((n) => {
              const href = `/collectors/logs?shape=${n.template_id}`
              return (
              <tr key={n.template_id} className="click" onClick={(e) => followRow(e, href, nav)} onAuxClick={(e) => followRow(e, href, nav)}>
                <td className="mono" style={{ fontSize: 12, wordBreak: 'break-word', maxWidth: 560 }}><Link className="row-link" to={href}>{n.pattern}</Link></td>
                <td><div className="row wrap" style={{ gap: 4 }}>{n.reasons.map((r) => <span key={r} className={`pill ${r === 'security' ? 'crit' : r === 'error' ? 'high' : 'medium'}`}>{r}</span>)}</div></td>
                <td className="num">{fmt.n(n.count)}</td><td className="faint" style={{ fontSize: 12 }}>{n.source}</td></tr>
              )
            })}</tbody></table></div>
        )}
      </Card>
      <Card title="Search raw lines" hint="Recent lines are kept for drill-down" pad>
        <form className="row" style={{ alignItems: 'flex-end' }} onSubmit={(e) => { e.preventDefault(); if (q.length > 1) run('q', () => api(`/api/logs/search?q=${encodeURIComponent(q)}`)).then(setHits) }}>
          <Field label="Search raw lines"><input id="log-search" className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="For example: denied" /></Field>
          <button className="btn">Search</button></form>
        {hits && (hits.length === 0 ? <Empty title="No matches" /> : <pre className="code" style={{ marginTop: 12 }}>{hits.map((h) => `${fmt.time(h.ts)}  ${h.message}`).join('\n')}</pre>)}
      </Card>
      {shape && <Modal title="Lines with this shape" wide onClose={closeShape}>
        <Field label="Shape"><div className="mono" style={{ fontSize: 12 }}>{shape.pattern}</div></Field>
        <p className="muted" style={{ margin: '8px 0' }}>{fmt.n(shape.count)} lines since {fmt.time(shape.first_seen)}</p>
        <pre className="code">{shape.lines.map((l) => l.message).join('\n')}</pre></Modal>}
    </div>
  )
}
