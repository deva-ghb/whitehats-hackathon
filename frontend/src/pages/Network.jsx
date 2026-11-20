import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { CollectorTabs } from './Collectors'
import { Card, Empty, ErrorNote, Loading, Sev, Stat, Status, Tabs, useAction } from '../components/ui'

export default function Network() {
  const { user } = useAuth()
  const { data, error, loading, reload } = useApi('/api/network/overview', { poll: 10000 })
  const [tab, setTab] = useState('open')
  const [busy, run] = useAction()
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const s = data.summary
  const findings = data.findings.filter((f) => tab === 'all' || f.status === tab)
  const setStatus = (f, status) => run(`f${f.id}`, () => api(`/api/network/findings/${f.id}`, { method: 'PATCH', body: { status } }), 'Updated').then(reload)
  return (
    <div className="stack" style={{ gap: 16 }}>
      <CollectorTabs on="/collectors/network" />
      <div className="page-head"><div><h2>What is open, and who is talking</h2><p>Built from live snapshots sent by network analyzers. Findings close themselves when the exposure disappears.</p></div>
        <Link to="/collectors" className="btn">Network analyzers</Link></div>
      {data.raw_hidden && <div className="banner info"><div>{data.raw_hidden} The findings below are this project's.</div></div>}
      {s && s.connections === 0 && <div className="banner info"><div>No network data yet. Deploy a <b>Network analyzer</b> from Collectors. "Run here now" starts one on this server.</div></div>}
      <div className="grid g4">
        {s && <Stat label="Live connections" value={fmt.n(s.established)} note={`${fmt.n(s.public_destinations)} public destinations`} />}
        {s && <Stat label="Listening ports" value={fmt.n(s.listening)} note={`${s.exposed} open on every interface`} tone={s.exposed ? 'warn' : undefined} />}
        <Stat label="Open findings" value={data.findings.filter((f) => f.status === 'open').length} tone={data.findings.some((f) => f.status === 'open' && f.severity === 'high') ? 'crit' : undefined} />
        {s && <Stat label="Last snapshot" value={fmt.ago(data.observed_at)} />}
      </div>
      <Card title="Findings">
        <div style={{ padding: '0 20px' }}><Tabs tabs={[['open', 'Open'], ['resolved', 'Resolved'], ['accepted', 'Accepted'], ['all', 'All']]} value={tab} onChange={setTab} /></div>
        {findings.length === 0 ? <Empty title="Nothing here" /> : findings.map((f) => (
          <div key={f.id} className="list-item">
            <div className="row between"><div className="row"><Sev s={f.severity} /><b>{f.title}</b></div><Status s={f.status} /></div>
            <p className="muted" style={{ fontSize: 13, marginTop: 4 }}>{f.detail}</p>
            <div className="row between" style={{ marginTop: 6 }}><span className="faint" style={{ fontSize: 12 }}>{f.host} · first seen {fmt.ago(f.first_seen)} · last seen {fmt.ago(f.last_seen)}</span>
              {f.status === 'open' && allowed(user, 'triage') && <button className="btn small" disabled={busy === `f${f.id}`} onClick={() => setStatus(f, 'accepted')}>Accept the risk</button>}</div>
          </div>
        ))}
      </Card>
      {s && <>
      <div className="grid g2">
        <Card title="Busiest processes">{s.top_processes.length === 0 ? <Empty title="No data" /> : s.top_processes.map((p) => (
          <div key={p.process} className="list-item row between"><span>{p.process}</span><b className="num">{p.connections}</b></div>))}</Card>
        <Card title="Most-contacted public addresses">{s.top_destinations.length === 0 ? <Empty title="No data" /> : s.top_destinations.map((p) => (
          <div key={p.address} className="list-item row between"><span className="mono">{p.address}</span><b className="num">{p.connections}</b></div>))}</Card>
      </div>
      <Card title="Connections" hint="Latest snapshot">
        <div className="tbl-wrap" style={{ maxHeight: 420, overflow: 'auto' }}><table className="t"><thead><tr><th>Process</th><th>State</th><th>Local</th><th>Remote</th><th>Proto</th></tr></thead>
          <tbody>{data.flows.map((f, i) => <tr key={i}><td>{f.process || '—'}<span className="faint mono" style={{ fontSize: 11 }}> {f.pid || ''}</span></td><td>{f.state}</td>
            <td className="mono" style={{ fontSize: 12 }}>{f.local_addr}:{f.local_port}</td><td className="mono" style={{ fontSize: 12 }}>{f.remote_addr ? `${f.remote_addr}:${f.remote_port}` : '—'}</td><td>{f.proto}</td></tr>)}</tbody></table></div>
      </Card>
      </>}
    </div>
  )
}
