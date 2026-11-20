import { useState } from 'react'
import KnowledgeGraph from './KnowledgeGraph'
import { Link } from 'react-router-dom'
import { api, fmt, getProjectSlug, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Empty, ErrorNote, Icon, Loading, Select, useAction } from '../components/ui'
import SphereGraph from './SphereGraph'
import CloudRule, { opWords, settingWords } from './CloudRule'

// KS.1: what WhiteHats has learned about the business, from approvers' decisions. Read-only: nothing here is drawn or
// edited by hand. The only actions are an approver confirming or rejecting what WhiteHats proposed.

const KIND_LABEL = { Concept: 'Concept', Rule: 'Business rule', Exception: 'Accepted exception' }
const PROTECTS = { money: 'Money', data: 'Data', access: 'Access', availability: 'Availability', other: 'Other' }
const WORKING = {
  propose: 'proposing from an audit', fix_approved: 'learning the rule an approved fix protects',
  not_affected: 'recording an accepted exception', network_accepted: 'recording an accepted exception',
  reversed: 'forgetting what a reversed decision taught', confirm: 'confirming proposals', reject: 'dropping proposals',
}
function citeText(c) {
  if (c.kind === 'file') return c.lines?.length === 2 && c.lines[0] ? `${c.path}, lines ${c.lines[0]} to ${c.lines[1]}` : c.path
  if (c.kind === 'node') return c.name || c.arn
  if (c.kind === 'finding') return `Finding: ${c.title}`
  return c.label || c.ref
}

export default function Sphere() {
  const { user, org } = useAuth()
  const Graph = org?.local_workspace ? KnowledgeGraph : SphereGraph
  const [version, setVersion] = useState('')
  const [compare, setCompare] = useState('none')
  const [offset, setOffset] = useState(0)
  const query = new URLSearchParams()
  if (offset) query.set('offset', String(offset))
  if (version) query.set('version', version)
  if (compare !== 'none') query.set('compare', compare)
  const qs = query.toString()
  const { data, error, loading, reload } = useApi(`/api/sphere${qs ? `?${qs}` : ''}`, { poll: 5000 })
  const history = useApi('/api/sphere/versions', { poll: 15000 })
  const [picked, setPicked] = useState(null)
  const [adding, setAdding] = useState(false)
  const [busy, run] = useAction()
  const approver = allowed(user, 'approve')

  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />

  const forgotten = (data.forgotten || []).map((n) => ({ ...n, forgotten: true }))
  const nodes = [...data.nodes, ...forgotten]
  const byKey = Object.fromEntries(nodes.map((n) => [n.key, n]))
  const selected = picked && byKey[picked] ? byKey[picked] : null
  const proposed = data.proposed || data.nodes.filter((n) => n.status === 'proposed')
  const proposalNodes = { ...Object.fromEntries((data.proposed_concepts || []).map((n) => [n.key, n])), ...byKey }
  const versions = history.data || []
  const propose = () => run('propose', () => api('/api/sphere/propose', { method: 'POST' }), 'WhiteHats is proposing from the latest audit').then(reload)

  return (
    <div className="sphere">
      <div className="sphere-bar" role="toolbar" aria-label="Knowledge controls">
        <Link className="btn" to={`/p/${getProjectSlug()}/compliance`}>{org?.local_workspace ? "Compliance Knowledge" : "Compliance Sphere"}</Link>
        <Select id="sphere-version" label="Version" value={version || String(data.latest)} onChange={(v) => { setVersion(Number(v) === data.latest ? '' : v); setOffset(0) }}
          disabled={!data.latest}
          options={versions.length ? versions.map((v) => ({ value: String(v.number), label: `Version ${v.number}${v.number === data.latest ? ' (newest)' : ''}`, description: v.reason }))
            : [{ value: String(data.latest), label: data.latest ? `Version ${data.latest}` : 'No versions yet' }]} />
        <Select id="sphere-compare" label="Compare with" value={compare} onChange={(v) => { setCompare(v); setOffset(0) }} disabled={data.version < 1}
          options={[{ value: 'none', label: 'Nothing' }, ...Array.from({ length: data.version }, (_, i) => data.version - 1 - i).map((n) => ({
            value: String(n), label: n === 0 ? 'Before anything was learned' : `Version ${n}` }))]} />
        <div className="sphere-bar-end">
          {approver && <button className="btn small" onClick={() => setAdding(true)}><Icon.plus size={14} /> <span className="btn-label">Add a cloud rule</span></button>}
          {approver && <button className="btn small" disabled={busy === 'propose'} onClick={propose}><Icon.spark size={14} /> <span className="btn-label">Propose from the latest audit</span></button>}
        </div>
      </div>
      <Working working={data.working} lastError={data.last_error} />
      {data.diff && <DiffLegend diff={data.diff} compare={data.compare} version={data.version} />}
      {!data.nodes.length && !forgotten.length ? (
        <div className="sphere-empty">
          <Empty title="Nothing learned yet">
            WhiteHats builds this from your approvers&apos; decisions: the rules a fix protects, the findings marked safe, and what it proposed from the first audit once someone confirms it. Pull requests and cloud changes are judged against it but never change it.
          </Empty>
        </div>
      ) : (
        <div className="sphere-body">
          <Graph nodes={nodes} edges={data.edges} diff={data.diff} selected={selected?.key} onPick={setPicked} stateOf={stateOf} label="Knowledge graph" />
          <Inspector node={selected} nodes={byKey} edges={data.edges} diff={data.diff} onPick={setPicked} approver={approver} onChanged={() => { setPicked(null); reload() }} />
        </div>
      )}
      {adding && <CloudRule onClose={() => setAdding(false)} onDone={reload} />}
      <Pager page={data.page} onMove={(at) => { setOffset(at); setPicked(null) }} />
      {proposed.length > 0 && data.version === data.latest && <Proposals proposed={proposed} nodes={proposalNodes} edges={data.proposed_edges || data.edges} approver={approver} onDone={reload} onPick={setPicked} />}
      <p className="faint sphere-foot">
        {data.latest ? `Version ${data.version} of ${data.latest}.` : 'No versions yet.'} Only approvers&apos; decisions change it; anything whose code or cloud resource is gone is forgotten.
      </p>
    </div>
  )
}

function Working({ working, lastError }) {
  if (working?.length) {
    const what = [...new Set(working.map((w) => WORKING[w.kind] || 'learning'))].join(', ')
    return <div className="banner info slim sphere-working" role="status"><span className="spin" /> <div>WhiteHats is {what}…</div></div>
  }
  if (lastError) return <div className="banner warn slim" role="note"><div>The last decision could not be learned: {lastError}</div></div>
  return null
}

function DiffLegend({ diff, compare, version }) {
  const from = Number(compare) === 0 ? 'the start' : `version ${compare}`
  return (
    <div className="sphere-legend" role="note">
      <span>Since {from}, version {version}:</span>
      <span className="sphere-chip learned">{diff.learned.length} learned</span>
      <span className="sphere-chip changed">{diff.changed.length} changed</span>
      <span className="sphere-chip forgot">{diff.forgot.length} forgotten</span>
    </div>
  )
}

function stateOf(node, diff) {
  if (node.forgotten) return 'forgot'
  if (!diff) return ''
  if (diff.learned.includes(node.key)) return 'learned'
  if (diff.changed.includes(node.key)) return 'changed'
  return 'same'
}

// A large sphere comes a page at a time, so the globe stays smooth: each concept stays on one page with its rules.
function Pager({ page, onMove }) {
  if (!page || page.total <= page.limit) return null
  const { offset, limit, total } = page
  const pages = Math.ceil(total / limit)
  const at = Math.floor(offset / limit) + 1
  return (
    <nav className="sphere-pager" aria-label="Pages of the knowledge graph">
      <button type="button" className="btn small" disabled={offset === 0} onClick={() => onMove(Math.max(0, offset - limit))}>Previous</button>
      <span>Page {at} of {pages}: items {offset + 1} to {Math.min(total, offset + limit)} of {total}</span>
      <button type="button" className="btn small" disabled={offset + limit >= total} onClick={() => onMove(offset + limit)}>Next</button>
    </nav>
  )
}

function Inspector({ node, nodes, edges, diff, onPick, approver, onChanged }) {
  const [busy, run] = useAction()
  if (!node) {
    return (
      <aside className="sphere-inspector" aria-label="Details">
        <div className="sphere-inspector-empty">
          <b>Choose a concept, rule or exception</b>
          <p className="muted">Each one shows what it cites: the code and cloud resources it rests on, and the decisions that taught it. When everything it cites is gone, WhiteHats forgets it.</p>
          <ul className="sphere-key">
            <li><span className="sphere-swatch concept" /> Concept: a thing the business deals in</li>
            <li><span className="sphere-swatch rule" /> Business rule the code must keep</li>
            <li><span className="sphere-swatch exception" /> Accepted exception an approver decided</li>
            <li><span className="sphere-swatch proposed" /> Proposed, waiting for an approver</li>
          </ul>
        </div>
      </aside>
    )
  }
  const related = edges.filter((e) => e.source === node.key || e.target === node.key)
    .map((e) => ({ e, other: nodes[e.source === node.key ? e.target : e.source] })).filter((r) => r.other)
  const state = stateOf(node, diff)
  const evidence = (node.cites || []).filter((c) => c.kind === 'file' || c.kind === 'node' || c.kind === 'finding')
  const decisions = (node.cites || []).filter((c) => c.kind === 'decision')
  return (
    <aside className="sphere-inspector" aria-label="Details">
      <div className="sphere-inspector-head">
        <span className={`pill ${node.kind === 'Exception' ? 'warn' : node.kind === 'Rule' ? 'ok' : 'neutral'}`}>{KIND_LABEL[node.kind]}</span>
        <span className={`pill ${node.forgotten ? 'crit' : node.status === 'known' ? 'ok' : 'neutral'}`}>{node.forgotten ? 'Forgotten' : node.status === 'known' ? 'Known' : 'Proposed'}</span>
        {state === 'learned' && <span className="pill ok">Learned in this version</span>}
        {state === 'changed' && <span className="pill neutral">Changed in this version</span>}
      </div>
      <h2 className={node.forgotten ? 'struck' : ''}>{node.text}</h2>
      {node.detail && <p className="muted">{node.detail}</p>}
      {node.protects && <p><b>Breaking it costs:</b> {PROTECTS[node.protects] || node.protects}</p>}
      {node.expect && (
        <>
          <h3>Checked in the cloud every hour</h3>
          <p>{settingWords(node.expect.property)} {opWords(node.expect.op)}{node.expect.value !== undefined ? ` ${node.expect.value}` : ''}, on {node.expect.label || node.expect.on.join(', ')}.</p>
          {approver && <button className="btn small" disabled={busy === 'forget'} onClick={() => run('forget', () => api('/api/sphere/cloud-rules/forget', { method: 'POST', body: { keys: [node.key] } }), 'Rule withdrawn').then((r) => r && onChanged())}>Withdraw this rule</button>}
        </>
      )}
      <h3>Rests on</h3>
      {evidence.length ? <ul className="sphere-cites">{evidence.map((c, i) => <li key={i}><Icon.code size={14} /> <span>{citeText(c)}</span></li>)}</ul>
        : <p className="faint">Nothing in the code or the account; it rests only on decisions.</p>}
      {node.lost?.length > 0 && (
        <>
          <h3>No longer there</h3>
          <ul className="sphere-cites lost">{node.lost.map((c, i) => <li key={i}><span className="struck">{citeText(c)}</span></li>)}</ul>
        </>
      )}
      <h3>Decided by</h3>
      {decisions.length ? <ul className="sphere-cites">{decisions.map((c, i) => <li key={i}><Icon.check size={14} /> <span>{citeText(c)}{c.at ? `, ${fmt.ago(c.at)}` : ''}</span></li>)}</ul>
        : <p className="faint">No approver has confirmed it yet.</p>}
      {related.length > 0 && (
        <>
          <h3>Connected to</h3>
          <ul className="sphere-related">
            {related.map(({ e, other }) => (
              <li key={`${e.kind}${other.key}`}>
                <button type="button" className="sphere-link" onClick={() => onPick(other.key)}>
                  <span className="faint">{e.kind === 'ABOUT' ? (e.source === node.key ? 'About' : 'Rule or exception') : e.kind === 'EXEMPTS' ? (e.source === node.key ? 'Exception to' : 'Exception') : 'Related'}:</span> {other.text}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  )
}

function Proposals({ proposed, nodes, edges, approver, onDone, onPick }) {
  const [chosen, setChosen] = useState(() => new Set(proposed.map((n) => n.key)))
  const [busy, run] = useAction()
  const about = (key) => edges.find((e) => e.kind === 'ABOUT' && e.source === key)?.target
  const groups = {}
  for (const n of proposed) {
    const home = n.kind === 'Concept' ? n.key : about(n.key)
    const label = home && nodes[home] ? nodes[home].text : 'Other'
    ;(groups[label] = groups[label] || []).push(n)
  }
  const keys = [...chosen].filter((k) => proposed.some((n) => n.key === k))
  const decide = (verb) => run(verb, () => api(`/api/sphere/${verb}`, { method: 'POST', body: { keys } }),
    verb === 'confirm' ? `Confirmed ${keys.length}` : `Rejected ${keys.length}`).then(onDone)
  const flip = (key) => setChosen((was) => { const next = new Set(was); if (next.has(key)) next.delete(key); else next.add(key); return next })
  return (
    <section className="card sphere-proposals" aria-labelledby="sphere-proposals-title">
      <div className="card-head">
        <div><h2 id="sphere-proposals-title">WhiteHats proposes {proposed.length} {proposed.length === 1 ? 'thing' : 'things'} your business depends on</h2>
          <div className="hint">{approver ? 'Confirm what is true; reject what is not. Nothing proposed is used as grounds for a finding until it is confirmed.' : 'An approver confirms or rejects these.'}</div></div>
        {approver && (
          <div className="row">
            <button className="btn small" disabled={!keys.length || !!busy} onClick={() => decide('reject')}>Reject {keys.length || ''}</button>
            <button className="btn small primary" disabled={!keys.length || !!busy} onClick={() => decide('confirm')}>Confirm {keys.length || ''}</button>
          </div>
        )}
      </div>
      <div className="card-body sphere-proposal-groups">
        {Object.entries(groups).map(([label, list]) => (
          <fieldset key={label} className="sphere-group">
            <legend>{label}</legend>
            {list.map((n) => (
              <div key={n.key} className="sphere-proposal">
                {approver && <input type="checkbox" id={`p-${n.key}`} checked={chosen.has(n.key)} onChange={() => flip(n.key)} />}
                <label htmlFor={`p-${n.key}`}>
                  <span className="faint">{KIND_LABEL[n.kind]}{n.protects ? ` · ${PROTECTS[n.protects]}` : ''}</span> {n.text}
                </label>
                <button type="button" className="btn ghost flush small" onClick={() => onPick(n.key)}>Show</button>
              </div>
            ))}
          </fieldset>
        ))}
      </div>
    </section>
  )
}
