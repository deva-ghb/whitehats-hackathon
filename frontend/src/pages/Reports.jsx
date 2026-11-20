import { useState } from 'react'
import { fmt, useApi } from '../lib/api'
import { Card, ErrorNote, Gauge, LineChart, Loading, Stat, Tabs, chartHint } from '../components/ui'

export default function Reports() {
  const [days, setDays] = useState('30')
  const [scope, setScope] = useState('project')
  const account = scope === 'account' ? '&account=true' : ''
  const { data, error, loading, reload } = useApi(`/api/reports/board?days=${days}${account}`, { deps: [days, scope] })
  if (loading && !data) return <Loading rows={8} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const f = data.facts
  return (
    <div className="stack" style={{ gap: 16, maxWidth: 1000 }}>
      <div className="page-head">
        <div><h2>Cyber risk report · {data.org}</h2><p>For the board. Generated {fmt.time(data.generated_at)} from live data; narrative by {data.model}.</p></div>
        <div className="row wrap" style={{ gap: 8 }}>
          <Tabs tabs={[['project', 'This project'], ['account', 'Whole account']]} value={scope} onChange={setScope} />
          <Tabs tabs={[['7', '7 days'], ['30', '30 days'], ['90', '90 days']]} value={days} onChange={setDays} />
        </div>
      </div>
      <Card><div className="card-body" style={{ display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', gap: 24, alignItems: 'center' }}>
        <div style={{ textAlign: 'center' }}><Gauge score={f.posture.score} grade={f.posture.grade} /><div className="pill brand">{f.posture.grade}</div>
          {f.lowest && <p>Lowest project: {f.lowest.name}, score {f.lowest.score}.</p>}
        </div>
        <div className="stack" style={{ gap: 12 }}>{data.narrative.split('\n').filter(Boolean).map((p, i) => <p key={i} style={{ fontSize: 15, lineHeight: 1.65 }}>{p}</p>)}</div>
      </div></Card>
      <div className="grid g4">
        <Stat label="Incidents" value={f.incidents.total} note={`${f.incidents.resolved} resolved · ${f.incidents.critical} critical`} />
        <Stat label="Fixes approved" value={f.fixes.approved} note={`${f.fixes.pending} waiting · ${f.fixes.rejected} rejected`} />
        <Stat label="Watching" value={f.coverage.collectors} note={`collectors · ${f.coverage.cloud_sources} of them cloud log sources`} />
        <Stat label="Network exposure" value={f.network.open} note={`${f.network.resolved} closed`} />
      </div>
      <Card title="Posture trend" hint={chartHint(f.history, f.history_started, fmt.date)}><div style={{ padding: '6px 14px 12px' }}><LineChart width={900} height={200} points={f.history.map((h) => ({ value: h.score, label: fmt.date(h.day), at: h.day }))} label="Posture" /></div></Card>
      <Card title="Where the score is lost" pad>
        <table className="t"><thead><tr><th>Area</th><th>Points lost</th><th>Detail</th></tr></thead>
          <tbody>{f.posture.breakdown.map((b) => <tr key={b.area}><td><b>{b.area}</b></td><td className="num">{b.penalty}</td><td className="muted">{b.detail}</td></tr>)}</tbody></table>
      </Card>
      {f.factory && <Card title="Fix Factory" pad><p>{f.factory.verified} fixes proven in {f.factory.runs} live runs, {f.factory.approved} approved and merged. Average AI cost per proven fix: ${Number(f.factory.cost_per_fix || 0).toFixed(2)}.</p></Card>}
    </div>
  )
}
