import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, CodeBlock, Empty, ErrorNote, Field, Icon, Loading, Modal, Stat, Status, Tabs, useAction } from '../components/ui'
import { followRow } from '../lib/rows'

const KINDS = [
  ['log', 'Log collector', 'Reads log files and ships new lines. The analyzer turns them into a short digest.'],
  ['network', 'Network analyzer', 'Snapshots open ports and live connections. Flags exposed services and odd destinations.'],
  ['process', 'Process monitor', 'Snapshots the busiest processes on a host.'],
]
const MODES = [
  ['local', 'Run here now', 'Starts the collector on this server immediately. Best for a first look.'],
  ['kubernetes', 'Kubernetes pods', 'A DaemonSet puts one read-only pod on every node.'],
  ['docker', 'Docker', 'One container per host, read-only, no capabilities.'],
  ['systemd', 'Linux service', 'A hardened systemd unit for plain servers.'],
]

const COLLECTOR_DATA_NOTE = 'What a collector sent is raw account data, not tied to one project yet. Account owners, Account admins, Security leads and Auditors can open a collector.'

const SECTIONS = [['/collectors', 'Collectors'], ['/collectors/logs', 'Logs'], ['/collectors/network', 'Network']]

/** One place for what the collectors send: the collectors themselves, the log digest, and the network they see. */
export function CollectorTabs({ on }) {
  const nav = useNavigate()
  return <Tabs tabs={SECTIONS} value={on} onChange={(to) => nav(to)} />
}

export default function Collectors() {
  const nav = useNavigate()
  const { user } = useAuth()
  const { data, error, loading, reload } = useApi('/api/collectors', { poll: 5000 })
  const [deploying, setDeploying] = useState(false)
  if (loading && !data) return <Loading rows={5} />
  if (error) return <ErrorNote error={error} retry={reload} />
  // What a collector sent is the account's raw data, so only the roles that see every project open one.
  const rawReader = allowed(user, 'read_account_data')
  const online = data.filter((c) => c.health === 'online').length
  const events = data.reduce((s, c) => s + c.events_total, 0)
  const budget = data.reduce((s, c) => s + (c.health !== 'stopped' ? c.context_budget : 0), 0)
  return (
    <div className="stack" style={{ gap: 16 }}>
      <CollectorTabs on="/collectors" />
      <div className="page-head">
        <div><h2>Collectors and pods</h2><p>Small read-only agents that send data back. Each one has a context budget: the most it may add to what the AI reads, however much data it collects.</p></div>
        {allowed(user, 'manage_connections') && <button className="btn primary" onClick={() => setDeploying(true)}><Icon.plus size={16} /> Deploy a collector</button>}
      </div>
      <div className="grid g4">
        <Stat label="Collectors online" value={`${online}/${data.length}`} tone={online === data.length ? 'good' : 'warn'} />
        <Stat label="Events collected" value={fmt.n(events)} note="lines, connections and samples" />
        <Stat label="AI context budget in use" value={fmt.n(budget)} unit="tokens" note="shared by all running collectors" />
        <Stat label="Data received" value={fmt.tokens(data.reduce((s, c) => s + c.bytes_total, 0))} unit="bytes" />
      </div>
      <Card>
        {data.length === 0 ? <Empty title="No collectors yet" action={allowed(user, 'manage_connections')
          ? <button className="btn primary" onClick={() => setDeploying(true)}>Deploy the first one</button> : null}>
          {allowed(user, 'manage_connections')
            ? 'Start with a log collector on this server. It is running in under ten seconds.'
            : 'An Account owner or Account admin deploys collectors. Ask one of them to start a log collector.'}</Empty> : (
          <div className="tbl-wrap"><table className="t"><thead><tr><th>Collector</th><th>Type</th><th>Runs as</th><th>Health</th><th>Host</th><th>Events</th><th>Context budget</th><th>Last heard</th></tr></thead>
            <tbody>{data.map((c) => {
              const href = `/collectors/${c.id}`
              const name = <><b>{c.name}</b>{c.pid && <div className="faint mono" style={{ fontSize: 11 }}>pid {c.pid}</div>}</>
              return (
              <tr key={c.id} className={rawReader ? 'click' : undefined} onClick={rawReader ? (e) => followRow(e, href, nav) : undefined} onAuxClick={rawReader ? (e) => followRow(e, href, nav) : undefined}>
                <td>{rawReader ? <Link className="row-link" to={href}>{name}</Link> : name}</td><td>{c.kind_label}</td><td>{MODES.find((m) => m[0] === c.mode)?.[1]}</td>
                <td><span className="row"><span className={`dot ${c.health === 'online' ? 'good pulse' : c.health === 'late' ? 'warn' : ['offline', 'crashed'].includes(c.health) ? 'crit' : 'neutral'}`} />{c.health}</span></td>
                <td>{c.host || <span className="faint">waiting for first contact</span>}</td><td className="num">{fmt.n(c.events_total)}</td>
                <td className="num">{fmt.n(c.context_budget)} tokens</td>
                <td className="num">{fmt.ago(c.last_heartbeat)}</td>
              </tr>
              )
            })}</tbody></table></div>
        )}
        {data.length > 0 && !rawReader && <p className="faint" style={{ fontSize: 12.5, padding: '12px 20px' }}>{COLLECTOR_DATA_NOTE}</p>}
      </Card>
      <Card title="How collectors stay inside the AI's context window" pad>
        <div className="grid g3">
          <div><b>1. Shape, not lines</b><p className="muted" style={{ marginTop: 4 }}>Each line is reduced to its shape by masking numbers, IDs and addresses. A million lines usually collapse into a few hundred shapes with counts.</p></div>
          <div><b>2. Only what changed</b><p className="muted" style={{ marginTop: 4 }}>Shapes are scored against their own 24-hour baseline. New shapes, spikes, errors and security words rise; routine traffic becomes one line.</p></div>
          <div><b>3. A budget per collector</b><p className="muted" style={{ marginTop: 4 }}>Each collector's notable shapes are packed into its own token budget, then all collectors into the digest budget. The AI sees a page, never the flood.</p></div>
        </div>
      </Card>
      {deploying && <DeployModal onClose={() => setDeploying(false)} onDone={() => reload()} />}
    </div>
  )
}

function DeployModal({ onClose, onDone }) {
  const [f, setF] = useState({ name: '', kind: 'log', mode: 'local', interval_s: 15, context_budget: 600, files: '' })
  const [result, setResult] = useState(null)
  const [busy, run] = useAction()
  const deploy = () => run('d', () => api('/api/collectors', {
    method: 'POST', body: { ...f, interval_s: Number(f.interval_s), context_budget: Number(f.context_budget), files: f.kind === 'log' && f.files.trim() ? f.files.split('\n').map((s) => s.trim()).filter(Boolean) : null },
  }), (r) => r.collector.mode === 'local' ? 'Collector started' : 'Collector created').then((r) => { if (r) { setResult(r); onDone() } })
  return (
    <Modal title={result ? `${result.collector.name} is ready` : 'Deploy a collector'} onClose={onClose} wide
      footer={result ? <button className="btn primary" onClick={onClose}>Done</button> : <><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy === 'd' || f.name.length < 2} onClick={deploy}>{busy === 'd' ? <span className="spin" /> : f.mode === 'local' ? 'Start it now' : 'Create and get the files'}</button></>}>
      {result ? <DeployResult r={result} /> : (
        <div className="stack" style={{ gap: 16 }}>
          <Field label="Name"><input id="col-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="payments-prod logs" /></Field>
          <div><b style={{ fontSize: 12.5 }}>What should it collect?</b>
            <div className="grid g3" style={{ marginTop: 8 }}>{KINDS.map(([k, t, d]) => (
              <button key={k} type="button" className="card card-pad" style={{ textAlign: 'left', cursor: 'pointer', borderColor: f.kind === k ? 'var(--brand)' : undefined, boxShadow: f.kind === k ? '0 0 0 2px var(--brand-soft)' : undefined }} onClick={() => setF({ ...f, kind: k })}>
                <b>{t}</b><div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>{d}</div></button>))}</div></div>
          <div><b style={{ fontSize: 12.5 }}>Where should it run?</b>
            <div className="grid g4" style={{ marginTop: 8 }}>{MODES.map(([k, t, d]) => (
              <button key={k} type="button" className="card card-pad" style={{ textAlign: 'left', cursor: 'pointer', borderColor: f.mode === k ? 'var(--brand)' : undefined, boxShadow: f.mode === k ? '0 0 0 2px var(--brand-soft)' : undefined }} onClick={() => setF({ ...f, mode: k })}>
                <b>{t}</b><div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{d}</div></button>))}</div></div>
          <div className="grid g2">
            <Field label="Report every (seconds)"><input id="col-interval" className="input" type="number" min="5" value={f.interval_s} onChange={(e) => setF({ ...f, interval_s: e.target.value })} /></Field>
            <Field label="AI context budget (tokens)" help="The most this collector may add to any AI prompt."><input id="col-budget" className="input" type="number" min="100" step="100" value={f.context_budget} onChange={(e) => setF({ ...f, context_budget: e.target.value })} /></Field>
          </div>
          {f.kind === 'log' && <Field label="Log files (optional, one per line)" help="Leave empty to use this platform's own log plus readable system logs.">
            <textarea id="col-files" className="input mono" value={f.files} onChange={(e) => setF({ ...f, files: e.target.value })} placeholder="/var/log/auth.log" /></Field>}
        </div>
      )}
    </Modal>
  )
}

function DeployResult({ r }) {
  const [tab, setTab] = useState(r.collector.mode === 'local' ? 'kubernetes' : r.collector.mode)
  return (
    <div className="stack">
      {r.collector.mode === 'local'
        ? <div className="banner good">Running on this server as process {r.collector.pid}. It will show as online within {r.collector.interval_s} seconds.</div>
        : <div className="banner warn"><div><b>Copy the token now.</b> It is shown once; only a fingerprint is stored. The files below already contain it.</div></div>}
      <CodeBlock title="Collector token" text={r.token} />
      <Tabs tabs={[['kubernetes', 'Kubernetes'], ['docker', 'Docker'], ['systemd', 'systemd'], ['dockerfile', 'Image']]} value={tab} onChange={setTab} />
      <CodeBlock title={{ kubernetes: 'kubectl apply -f whitehats-collector.yaml', docker: 'Run on each host', systemd: `/etc/systemd/system/whitehats-${r.collector.kind}-collector.service`, dockerfile: 'Dockerfile of the published image' }[tab]} text={r.manifests[tab]} />
    </div>
  )
}

export function CollectorDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const { user } = useAuth()
  const { data, error, loading, reload } = useApi(`/api/collectors/${id}`, { poll: 5000 })
  const [busy, run] = useAction()
  const [rotated, setRotated] = useState(null)
  const [tab, setTab] = useState('kubernetes')
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const operator = allowed(user, 'manage_connections')
  const act = (a) => run(a, () => api(`/api/collectors/${id}/${a}`, { method: 'POST' }), a === 'stop' ? 'Collector stopped' : 'New token issued').then((r) => { if (r?.token) setRotated(r); reload() })
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Link to="/collectors" className="muted">← Collectors</Link>
      <div className="page-head">
        <div><div className="row"><span className={`dot ${data.health === 'online' ? 'good pulse' : ['offline', 'crashed'].includes(data.health) ? 'crit' : 'neutral'}`} /><Status s={data.health} /><span className="pill neutral">{data.kind_label}</span></div>
          <h2 style={{ marginTop: 6 }}>{data.name}</h2><p>{data.host || 'Waiting for first contact'} · version {data.version || '—'} · every {data.interval_s}s · budget {data.context_budget} tokens</p></div>
        {operator && <div className="row">
          {!['stopped', 'crashed'].includes(data.status) ? <button className="btn" disabled={!!busy} onClick={() => act('stop')}>Stop</button> : <button className="btn primary" disabled={!!busy} onClick={() => act('start')}>{data.mode === 'local' ? 'Start again' : 'Re-enable (new token)'}</button>}
          <button className="btn" disabled={!!busy} onClick={() => act('rotate')}>Rotate token</button>
          {allowed(user, 'manage_connections') && <button className="btn danger" onClick={() => run('del', () => api(`/api/collectors/${id}`, { method: 'DELETE' }), 'Collector removed').then(() => nav('/collectors'))}>Remove</button>}
        </div>}
      </div>
      {rotated && <div className="card card-pad"><CodeBlock title="New token (shown once)" text={rotated.token} /></div>}
      {data.health === 'crashed' && <div className="banner crit"><div><b>This collector's process stopped unexpectedly.</b> Its output is in <span className="mono">{data.log_file}</span>. Press Start again once the cause is fixed.</div></div>}
      <div className="grid g4"><Stat label="Events" value={fmt.n(data.events_total)} /><Stat label="Data received" value={fmt.tokens(data.bytes_total)} unit="bytes" />
        <Stat label="Last heard" value={fmt.ago(data.last_heartbeat)} /><Stat label="Runs as" value={data.mode} /></div>
      <Card title="Latest data" hint="What this collector sent most recently">
        {!data.recent?.length ? <Empty title="Nothing yet">Data appears after the first report.</Empty> : (
          <div className="tbl-wrap" style={{ maxHeight: 420, overflow: 'auto' }}><table className="t">
            <thead><tr>{Object.keys(data.recent[0]).map((k) => <th key={k}>{k.replace('_', ' ')}</th>)}</tr></thead>
            <tbody>{data.recent.map((r, i) => <tr key={i}>{Object.entries(r).map(([k, v]) => <td key={k} className={k === 'message' ? 'mono' : 'num'} style={{ fontSize: 12.5, wordBreak: 'break-word' }}>{k === 'ts' ? fmt.time(v) : String(v)}</td>)}</tr>)}</tbody></table></div>
        )}
      </Card>
      <Card title="Deploy this collector elsewhere" pad>
        <Tabs tabs={[['kubernetes', 'Kubernetes'], ['docker', 'Docker'], ['systemd', 'systemd'], ['dockerfile', 'Image']]} value={tab} onChange={setTab} />
        <p className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>Replace the token placeholder with a fresh token from "Rotate token".</p>
        <CodeBlock title="" text={data.manifests[tab]} />
      </Card>
    </div>
  )
}
