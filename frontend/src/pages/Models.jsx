import { useState } from 'react'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Card, Empty, ErrorNote, Field, Icon, Loading, Modal, Select, Stat, Status, useAction } from '../components/ui'
import { providerOptions } from '../lib/choices'

const PURPOSE_LABEL = { assistant: 'Ask Big Hat', summarizer: 'Log digests', executive: 'CEO briefing and board report', triage: 'Triage', review: 'Independent review' }
const BLANK = { name: '', provider: 'azure_openai', endpoint: '', deployment: '', api_version: 'v1', api_key: '', roles: Object.keys(PURPOSE_LABEL), context_window: 128000, max_output: 1200, priority: 10, enabled: true, notes: '' }

export default function Models() {
  const { user, org } = useAuth()
  const { data, error, loading, reload } = useApi('/api/models')
  const [edit, setEdit] = useState(null)
  const [busy, run] = useAction()
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const admin = allowed(user, 'manage_ai')
  const local = org?.local_workspace
  const deps = local ? data.deployments.filter(d => d.provider === "vertex") : data.deployments
  const calls = local ? data.recent_calls.filter(c => deps.some(d => d.name === c.model)) : data.recent_calls
  const live = deps.filter((d) => d.enabled && d.provider !== 'rules' && d.status === 'ready')
  const tokens = deps.reduce((s, d) => s + d.usage_30d.tokens, 0)
  const test = (d) => run(`t${d.id}`, () => api(`/api/models/${d.id}/test`, { method: 'POST' }), (r) => r.ok ? `${d.name} answered in ${r.latency_ms} ms` : `${d.name}: ${r.message}`).then(reload)
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div><h2>Models and cost control</h2><p>Choose a model for each workflow and review usage and audit budgets.</p></div>
        {admin && <button className="btn primary" onClick={() => setEdit(local ? { ...BLANK, provider: 'vertex', deployment: 'gemini-3.1-flash-lite' } : { ...BLANK })}><Icon.plus size={16} /> Add a model</button>}
      </div>
      {live.length === 0 && <div className="banner warn"><div><b>No AI model is connected yet.</b> Answers come from the rules engine. Add the API key to <b>{deps[0]?.name}</b> and press Test to switch on full answers.</div></div>}
      <div className="grid g4">
        <Stat label="Models ready" value={live.length} note={`${deps.filter((d) => d.enabled).length} enabled`} tone={live.length ? 'good' : 'warn'} />
        <Stat label="Tokens, 30 days" value={fmt.tokens(tokens)} />
        <Stat label="AI calls, 30 days" value={fmt.n(deps.reduce((s, d) => s + d.usage_30d.calls, 0))} />
        {!local && <Stat label="Rules-engine answers" value={fmt.n(data.fallback_calls_30d)} note="when no model was reachable" />}
      </div>
      <Card title="Audit cost control" pad>
        <div className="grid g4">
          <Stat label="Recorded audit cost, 30 days" value={data.audit_cost_30d == null ? 'Unavailable' : '$' + data.audit_cost_30d.toFixed(2)} />
          <Stat label="Audit limit" value={data.audit_limit_usd == null ? 'Unavailable' : '$' + data.audit_limit_usd} />
          <Stat label="Pull request review limit" value={data.review_limit_usd == null ? 'Unavailable' : '$' + data.review_limit_usd} />
        </div>
      </Card>
      <div className="grid g2">
        {deps.map((d) => (
          <Card key={d.id}>
            <div className="card-body stack" style={{ gap: 8 }}>
              <div className="row between"><div><b style={{ fontSize: 15 }}>{d.name}</b><div className="muted" style={{ fontSize: 12.5 }}>{d.provider_label}</div></div>
                <div className="row">{!d.enabled && <span className="pill neutral">disabled</span>}<Status s={d.status} /></div></div>
              {d.provider !== 'rules' && <div className="mono faint" style={{ fontSize: 11.5, wordBreak: 'break-all' }}>{d.endpoint || 'local'}{d.deployment && ` · ${d.deployment}`}{d.provider === 'azure_openai' && ` · api ${d.api_version}`}</div>}
              <div className="row wrap" style={{ gap: 4 }}>{(d.roles.length ? d.roles : ['all']).map((r) => <span key={r} className="pill brand">{PURPOSE_LABEL[r] || 'Any purpose'}</span>)}</div>
              <div className="row wrap" style={{ fontSize: 12.5, gap: 14 }}>
                <span><span className="muted">Priority</span> {d.priority}</span><span><span className="muted">Context</span> {fmt.tokens(d.context_window)}</span>
                <span><span className="muted">Max output</span> {fmt.n(d.max_output)}</span><span><span className="muted">Key</span> {local ? 'Managed' : d.has_key ? d.key_hint : ['vertex', 'azure_openai', 'openai_compatible'].includes(d.provider) ? 'not set' : 'not needed'}</span>
                <span><span className="muted">30d</span> {d.usage_30d.calls} calls · {fmt.tokens(d.usage_30d.tokens)} tokens</span>
              </div>
              {d.notes && <p className="muted" style={{ fontSize: 12.5 }}>{d.notes}</p>}
              {d.last_error && <div className="banner crit" style={{ fontSize: 12 }}>{d.last_error}</div>}
              {admin && <div className="row">
                <button className="btn small" onClick={() => setEdit({ ...d, api_key: '' })}>{!local && d.provider !== 'rules' && !d.has_key && d.provider !== 'claude_cli' ? 'Add key' : 'Edit'}</button>
                <button className="btn small" disabled={busy === `t${d.id}`} onClick={() => test(d)}>{busy === `t${d.id}` ? <span className="spin" /> : 'Test'}</button>
                {d.provider !== 'rules' && <button className="btn small ghost" onClick={() => run('tog', () => api(`/api/models/${d.id}`, { method: 'PUT', body: { ...d, enabled: !d.enabled, api_key: null } }), d.enabled ? 'Disabled' : 'Enabled').then(reload)}>{d.enabled ? 'Disable' : 'Enable'}</button>}
              </div>}
            </div>
          </Card>
        ))}
      </div>
      <Card title="Recent AI calls" hint="Every call is recorded: model, purpose, tokens, time">
        {calls.length === 0 ? <Empty title="No calls yet" /> : (
          <div className="tbl-wrap"><table className="t"><thead><tr><th>When</th><th>Model</th><th>Purpose</th><th>Result</th><th>Tokens</th><th>Time</th></tr></thead>
            <tbody>{calls.map((c, i) => <tr key={i}><td className="num">{fmt.time(c.at)}</td><td>{c.model}</td><td>{PURPOSE_LABEL[c.purpose] || c.purpose}</td>
              <td>{c.fallback ? <span className="pill neutral">rules engine</span> : c.ok ? <span className="pill good">ok</span> : <span className="pill crit" title={c.error}>failed</span>}</td>
              <td className="num">{fmt.n(c.tokens)}</td><td className="num">{c.latency_ms ? `${(c.latency_ms / 1000).toFixed(1)}s` : '—'}</td></tr>)}</tbody></table></div>
        )}
      </Card>
      {edit && <ModelModal value={edit} purposes={data.purposes} providers={local ? {vertex: data.providers.vertex} : data.providers} local={local} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload() }} />}
    </div>
  )
}

function ModelModal({ value, purposes, providers, local, onClose, onSaved }) {
  const [f, setF] = useState(value)
  const [busy, run] = useAction()
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'number' ? Number(e.target.value) : e.target.value })
  const save = () => run('save', () => api(f.id ? `/api/models/${f.id}` : '/api/models', { method: f.id ? 'PUT' : 'POST', body: { ...f, api_key: f.api_key || null } }), 'Saved. Press Test to check it.').then((d) => d && onSaved())
  const needsKey = !local && ['vertex', 'azure_openai', 'openai_compatible'].includes(f.provider)
  return (
    <Modal title={f.id ? `Edit ${f.name}` : 'Add a model'} onClose={onClose} wide footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy === 'save' || f.name.length < 2} onClick={save}>Save</button></>}>
      <div className="stack" style={{ gap: 14 }}>
        <div className="grid g2">
          <Field label="Name"><input id="m-name" className="input" value={f.name} onChange={set('name')} placeholder="Gemini Flash-Lite" /></Field>
          <Field label="Provider"><Select id="m-provider" value={f.provider} onChange={(provider) => setF({ ...f, provider })} options={providerOptions(providers)} /></Field>
        </div>
        {f.provider !== 'rules' && <div className="grid g2">
          {!['claude_cli', 'vertex'].includes(f.provider) && <Field label={f.provider === 'azure_openai' ? 'Azure endpoint' : 'Base URL'} help={f.provider === 'azure_openai' ? 'https://<resource>.openai.azure.com' : 'For example http://vllm.internal:8000/v1'}>
            <input id="m-endpoint" className="input" value={f.endpoint} onChange={set('endpoint')} /></Field>}
          <Field label={f.provider === 'azure_openai' ? 'Deployment name' : 'Model'}><input id="m-deployment" className="input" value={f.deployment} onChange={set('deployment')} placeholder={f.provider === 'claude_cli' ? 'sonnet' : f.provider === 'vertex' ? 'gemini-3.1-flash-lite' : 'Model ID'} /></Field>
          {f.provider === 'azure_openai' && <Field label="API version" help="v1 uses Azure's current API. Or a dated version such as 2025-04-01-preview."><input id="m-version" className="input" value={f.api_version} onChange={set('api_version')} /></Field>}
          {needsKey && <Field label="API key" help={f.has_key ? `Stored (${f.key_hint}). Leave blank to keep it.` : 'Stored encrypted. Never shown again.'}><input id="m-key" className="input" type="password" autoComplete="off" value={f.api_key || ''} onChange={set('api_key')} /></Field>}
        </div>}
        <Field label="Use this model for" help="Leave all unticked to use it for anything.">
          <div className="row wrap" style={{ gap: 12 }}>{purposes.map((p) => (
            <label key={p} className="row" style={{ gap: 6 }}><input type="checkbox" checked={f.roles.includes(p)} onChange={(e) => setF({ ...f, roles: e.target.checked ? [...f.roles, p] : f.roles.filter((r) => r !== p) })} />{PURPOSE_LABEL[p]}</label>))}</div>
        </Field>
        <div className="grid g3">
          <Field label="Priority" help="Lower is tried first"><input id="m-priority" className="input" type="number" value={f.priority} onChange={set('priority')} /></Field>
          <Field label="Context window (tokens)"><input id="m-context" className="input" type="number" value={f.context_window} onChange={set('context_window')} /></Field>
          <Field label="Max output (tokens)"><input id="m-output" className="input" type="number" value={f.max_output} onChange={set('max_output')} /></Field>
        </div>
        <Field label="Notes"><input id="m-notes" className="input" value={f.notes} onChange={set('notes')} /></Field>
      </div>
    </Modal>
  )
}
