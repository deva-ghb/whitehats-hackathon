import { Link } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Bar, Card, ErrorNote, Loading, Stat, Status, useAction } from '../components/ui'
import Explain from '../components/Explain'

function measuredCoverage(agent) {
  const cov = agent.coverage
  return typeof cov === 'number' ? cov : null
}

function isPlanned(item) {
  return item.available === false || item.info?.available === false
}

function WatchBadge({ agent }) {
  if (agent.watch === 'waiting') return <Explain term="waiting"><span className="pill warn wrap">Waiting for data</span></Explain>
  if (agent.watch === 'blind' && agent.status !== 'dark') return <Explain term="blind"><span className="pill crit wrap">Blind spot</span></Explain>
  return <Explain term={agent.status}><span className="explain-host"><Status s={agent.status} /></span></Explain>
}

export default function Agents() {
  const { user } = useAuth()
  const { data, error, loading, reload } = useApi('/api/agents', { poll: 30000 })
  const judged = useApi('/api/false-positives', { poll: 60000 }) // FD.1: how often approvers called its findings wrong
  const [busy, run] = useAction()
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const { agents, catalog, summary = {} } = data
  const roster = agents.filter((a) => !isPlanned(a))
  const watching = summary.watching ?? roster.filter((a) => a.watch === 'watching').length
  const blind = roster.filter((a) => a.watch !== 'watching')
  const blindCount = summary.blind_spots ?? blind.length
  const rosterCount = summary.roster ?? roster.length
  const coverages = roster.map(measuredCoverage)
  const measured = coverages.filter((c) => c != null) // only WhiteHats with something on the map to watch
  const plannedCards = [
    ...agents.filter(isPlanned).map((a) => ({
      key: a.key, name: a.name, description: a.info?.description || a.domain,
      unavailable_reason: a.info?.unavailable_reason || '',
    })),
    ...catalog.filter((w) => !w.available),
  ]
  const ready = catalog.filter((w) => w.available)
  const decider = allowed(user, 'edit_model')
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head"><div><h2>Many small defenders</h2><p>Each WhiteHat watches one area of the business and shares what it sees. A WhiteHat with no real data yet is waiting for data, and it counts as a blind spot.</p></div></div>
      {!decider && <div className="banner info">You can see every WhiteHat. Only an Engineer or a Project admin can switch one on or change it.</div>}
      <div className="grid g4">
        <Stat label={<Explain term="watching"><span>Watching</span></Explain>} value={`${watching}/${rosterCount}`} tone={watching < rosterCount ? 'warn' : 'good'} />
        <Stat label={<Explain term="blind"><span>Blind spots</span></Explain>} value={blindCount} note={blind.map((a) => a.name).join(', ') || 'none'} tone={blindCount ? 'crit' : 'good'} />
        <Stat label="Signals this week" value={fmt.n(roster.reduce((s, a) => s + a.signals_7d, 0))} />
        {measured.length
          ? <Stat label={<Explain term="coverage"><span>Average coverage</span></Explain>} value={Math.round(measured.reduce((s, c) => s + c, 0) / measured.length)} unit="%"
            note={`of what is on the map, across ${measured.length} WhiteHat${measured.length === 1 ? '' : 's'}`} tone={measured.some((c) => c < 50) ? 'warn' : 'good'} />
          : <div className="card stat"><div className="label">Average coverage</div><div className="value" style={{ fontSize: 18 }}>Nothing on the map yet</div></div>}
      </div>
      <div className="grid g3">
        {roster.map((a) => {
          const cov = measuredCoverage(a)
          return (
            <Card key={a.id}>
              <div className="card-body stack" style={{ gap: 8 }}>
                <div className="row between"><b style={{ fontSize: 15 }}>{a.name}</b><WatchBadge agent={a} /></div>
                <p className="muted" style={{ fontSize: 13 }}>{a.info?.description || a.domain}</p>
                {a.info && <div className="row wrap" style={{ gap: 6 }}>{a.info.data_sources.map((d) => <Explain key={d.key} term="data_source"><span className="pill neutral wrap">{d.label}</span></Explain>)}</div>}
                {!a.detects && <p className="muted" style={{ fontSize: 12 }}>Records signals sent to it; has no detection rules of its own yet.</p>}
                <div className="row between" style={{ fontSize: 12 }}><Explain term="coverage"><span className="muted">Coverage</span></Explain><b>{cov == null ? 'Nothing on the map to watch' : `${cov}%`}</b></div>
                {cov != null && <Bar value={cov} tone={cov < 50 ? 'crit' : cov < 80 ? 'warn' : 'good'} />}
                {cov != null && <p className="muted" style={{ fontSize: 12 }}>Sees {a.covers.seen} of {a.covers.total} on the map{a.covers.dark.length ? `. Not seen: ${a.covers.dark.join(', ')}${a.covers.total - a.covers.seen > a.covers.dark.length ? ' and more' : ''}` : ''}.</p>}
                <div className="row between" style={{ fontSize: 12.5 }}><Explain term="signals_week"><span>{a.signals_7d} signal{a.signals_7d === 1 ? '' : 's'} this week</span></Explain><span className="muted">{a.last_signal_at ? `last ${fmt.ago(a.last_signal_at)}` : 'none yet'}</span></div>
                <FalseRate counts={judged.data?.[`whitehat:${a.key}`]} />
                {(a.status === 'learning' || a.would_have_7d > 0) && (
                  <p style={{ fontSize: 12.5 }}><Explain term="would_have"><span>{a.would_have_7d} it would have raised while learning</span></Explain></p>
                )}
                {a.health?.unreadable_times > 0 && <p className="muted" style={{ fontSize: 12.5 }}>{a.health.unreadable_times} sign-in {a.health.unreadable_times === 1 ? 'line' : 'lines'} had no readable time, so {a.health.unreadable_times === 1 ? 'it was' : 'they were'} not judged.</p>}
                {decider && <div className="row" style={{ gap: 6 }}>
                  {['active', 'learning', 'dark'].filter((s) => s !== a.status).map((s) => (
                    <Explain key={s} term={`mark_${s}`}><button className="btn small" disabled={busy === `${a.id}`} onClick={() => run(`${a.id}`, () => api(`/api/agents/${a.id}`, { method: 'PATCH', body: { status: s } }), `${a.name} set to ${s}`).then(reload)}>Mark {s}</button></Explain>))}
                </div>}
              </div>
            </Card>
          )
        })}
      </div>
      <ScannerRates />
      {ready.length > 0 && <>
        <div className="page-head" style={{ marginTop: 8 }}><div><h3>Add a WhiteHat</h3><p>Switch on another specialist. It starts watching straight away from the data sources listed. <Link to="/help/developers/adding-a-whitehat">How to build a new one</Link></p></div></div>
        <div className="grid g3">{ready.map((w) => (
          <Card key={w.key}><div className="card-body stack" style={{ gap: 8 }}>
            <div className="row between"><b style={{ fontSize: 15 }}>{w.name}</b><span className="pill info">ready</span></div>
            <p className="muted" style={{ fontSize: 13 }}>{w.description}</p>
            <div className="row wrap" style={{ gap: 6 }}>{w.data_sources.map((d) => <Explain key={d.key} term="data_source"><span className="pill neutral wrap">{d.label}</span></Explain>)}</div>
            {decider && <button className="btn small primary" style={{ alignSelf: 'flex-start' }} disabled={busy === w.key} onClick={() => run(w.key, () => api('/api/agents', { method: 'POST', body: { key: w.key } }), `${w.name} switched on`).then(reload)}>Switch on</button>}
          </div></Card>))}</div>
      </>}
      {plannedCards.length > 0 && <>
        <div className="page-head" style={{ marginTop: 8 }}><div><h3>Planned</h3><p>These WhiteHats are not available yet. They have no coverage, no signal counts and no switch.</p></div></div>
        <div className="grid g3">{plannedCards.map((w) => (
          <Card key={w.key} className="wh-planned"><div className="card-body stack" style={{ gap: 8 }}>
            <div className="row between"><b style={{ fontSize: 15 }}>{w.name}</b><Explain term="planned"><span className="pill neutral">planned</span></Explain></div>
            <p className="muted" style={{ fontSize: 13 }}>{w.description}</p>
            {w.unavailable_reason && <p className="muted" style={{ fontSize: 12 }}>{w.unavailable_reason}</p>}
          </div></Card>
        ))}</div>
      </>}
    </div>
  )
}


/** How many of a detector's findings approvers called false positives: what tuning it should start from. */
function FalseRate({ counts }) {
  if (!counts?.raised) return null
  const share = Math.round((100 * counts.false_positives) / counts.raised)
  return (
    <div className="row between" style={{ fontSize: 12.5 }}>
      <span>False positives</span>
      <span className={counts.false_positives ? '' : 'muted'}>{counts.false_positives} of {counts.raised} findings{counts.false_positives ? ` (${share}%)` : ''}</span>
    </div>
  )
}

const SCANNER_NAMES = { 'whitehats:code': 'Code checks on each push', 'whitehats:floor': 'Weekly scan of every file', audit: 'Security audits',
  'whitehats:image': 'Container image scans', 'whitehats:advisory': 'New advisories', 'whitehats:setup': 'Setup checks' }

/** The scanners that are not WhiteHats of their own, with the same count. */
export function ScannerRates() {
  const judged = useApi('/api/false-positives', { poll: 60000 })
  const rows = Object.entries(judged.data || {}).filter(([source, c]) => SCANNER_NAMES[source] && c.raised)
  if (!rows.length) return null
  return (
    <Card title="Scanners" hint="How many of each scanner's findings approvers called false positives">
      <div className="tbl-wrap"><table className="t"><thead><tr><th>Scanner</th><th className="num">Findings</th><th className="num">False positives</th></tr></thead>
        <tbody>{rows.map(([source, c]) => (
          <tr key={source}><td>{SCANNER_NAMES[source]}</td><td className="num">{c.raised}</td><td className="num">{c.false_positives}</td></tr>
        ))}</tbody></table></div>
    </Card>
  )
}
