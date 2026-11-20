import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, copyText, fmt, startSupport, useApi } from '../lib/api'
import Checklist, { Milestones, ReadinessBar } from '../components/Checklist'
import { Card, CodeBlock, Empty, ErrorNote, Field, Icon, Loading, Modal, SampleTag, Select, Stat, Status, Tabs, useAction, useToast } from '../components/ui'
import { PLANS, ROLES, regionOptions } from '../lib/choices'
import { followRow } from '../lib/rows'

const STATUS_ORDER = ['onboarding', 'draft', 'live', 'suspended', 'offboarded', 'deleted']

export default function Operator() {
  const nav = useNavigate()
  const { data, error, loading, reload } = useApi('/api/operator/tenants', { poll: 20000 })
  const [filter, setFilter] = useState('all')
  if (loading && !data) return <Loading rows={6} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const attention = data.tenants.filter((t) => t.first_value !== 'good' && !['offboarded', 'deleted'].includes(t.status)).length
  const list = data.tenants.filter((t) => filter === 'all' || t.status === filter)
    .sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status))
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div><h2>From signed contract to live service</h2><p>Every customer in one place. Nothing goes live until its checks are green and both sides have signed off.</p></div>
        <Link to="/companies/new" className="btn primary"><Icon.plus size={16} /> New company</Link>
      </div>
      <div className="grid g4">
        <Stat label="Onboarding" value={data.counts.onboarding + data.counts.draft} note={`${data.counts.draft} still in draft`} />
        <Stat label="Live" value={data.counts.live} tone="good" />
        <Stat label="Needs attention" value={attention} note="first value slipping" tone={attention ? 'warn' : undefined} />
        <Stat label="Suspended or leaving" value={data.counts.suspended + data.counts.offboarded} />
      </div>
      <Card>
        <div style={{ padding: '0 20px' }}><Tabs tabs={[['all', 'All'], ['onboarding', 'Onboarding'], ['draft', 'Draft'], ['live', 'Live'], ['suspended', 'Suspended'], ['offboarded', 'Offboarded']]} value={filter} onChange={setFilter} /></div>
        {list.length === 0 ? <Empty title="No companies here" action={<Link to="/companies/new" className="btn primary">Create the first one</Link>} /> : (
          <div className="tbl-wrap"><table className="t"><thead><tr><th>Company</th><th>Status</th><th>Plan</th><th>Readiness</th><th>First value</th><th>Collectors</th><th>Last sign-in</th></tr></thead>
            <tbody>{list.map((t) => {
              const href = `/companies/${t.id}`
              return (
              <tr key={t.id} className="click" onClick={(e) => followRow(e, href, nav)} onAuxClick={(e) => followRow(e, href, nav)}>
                <td><Link className="row-link" to={href}><b>{t.name}</b><div className="faint mono" style={{ fontSize: 11 }}>{t.slug} · {t.region}</div></Link></td>
                <td><Status s={t.status} /></td><td>{t.plan}</td>
                <td className="num">{t.passed}/{t.total}{t.blocking > 0 && t.status !== 'live' && <div className="faint" style={{ fontSize: 11 }}>{t.blocking} blocking</div>}</td>
                <td><Status s={t.first_value === 'good' ? 'on_track' : t.first_value} /></td>
                <td className="num">{t.collectors_online}/{t.collectors_total}</td>
                <td className="num">{fmt.ago(t.last_login)}</td>
              </tr>
              )
            })}</tbody></table></div>
        )}
      </Card>
    </div>
  )
}

const BLANK = {
  name: '', slug: '', plan: 'growth', region: 'in-south', domain: '', mfa_required: true, sso_required: false,
  retention_log_days: 30, retention_run_days: 365, retention_audit_days: 730,
  ai_policy: { providers: ['azure_openai', 'rules'], zero_retention: true, agreed: true },
  contract: { order_ref: '', signed_at: '', start_date: '', owner_name: '', owner_email: '', security_contact: '', pilot: true, pass_marks: '', billing_contact: '', po_number: '' },
  contracted_agents: ['identity', 'endpoint', 'network', 'cloud'],
}

export function NewCompany() {
  const nav = useNavigate()
  const cat = useApi('/api/operator/catalog')
  const [f, setF] = useState(BLANK)
  const [step, setStep] = useState(1)
  const [busy, run] = useAction()
  if (!cat.data) return <Loading rows={6} />
  const c = cat.data
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'number' ? Number(e.target.value) : e.target.value })
  const setC = (k) => (e) => setF({ ...f, contract: { ...f.contract, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value } })
  const slugFrom = (name) => name.toLowerCase().split('').map((ch) => ('abcdefghijklmnopqrstuvwxyz0123456789'.includes(ch) ? ch : '-')).join('').split('-').filter(Boolean).join('-').slice(0, 40)
  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  const ok1 = f.name.length > 1 && f.slug.length > 1
  const ok2 = f.contract.owner_email.includes('@') && f.contract.security_contact.length > 3
  const create = () => run('c', () => api('/api/operator/tenants', { method: 'POST', body: f }), 'Company created as a draft').then((d) => d && nav(`/companies/${d.id}`))
  return (
    <div className="stack" style={{ gap: 16, maxWidth: 900 }}>
      <Link to="/" className="muted">← Companies</Link>
      <div className="page-head"><div><h2>New company</h2><p>Three steps. The company starts as a draft and nothing reaches it until the contract checks are ticked and its first admin is invited.</p></div></div>
      <div className="wizard-steps">{['Company and plan', 'Contract', 'Security, AI and WhiteHats'].map((s, i) => <span key={s} className={`pill ${step === i + 1 ? 'brand' : step > i + 1 ? 'good' : 'neutral'}`}>{i + 1}. {s}</span>)}</div>
      <Card><div className="card-body stack" style={{ gap: 14 }}>
        {step === 1 && <>
          <div className="grid g2">
            <Field label="Company name"><input id="nc-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value, slug: f.slug && f.slug !== slugFrom(f.name) ? f.slug : slugFrom(e.target.value) })} placeholder="Globex Payments" /></Field>
            <Field label="Short name" help="Lower-case letters, digits and hyphens."><input id="nc-slug" className="input mono" value={f.slug} onChange={set('slug')} /></Field>
            <Field label="Plan"><Select id="nc-plan" value={f.plan} onChange={(plan) => setF({ ...f, plan })} options={PLANS.filter((plan) => c.plans.includes(plan.value))} /></Field>
            <Field label="Data region" help={`This installation stores data in ${c.regions[c.installation_region]}.`}>
              <Select id="nc-region" value={f.region} onChange={(region) => setF({ ...f, region })} options={regionOptions(c.regions)} /></Field>
            <Field label="Email domain" help="Only addresses at this domain can be invited. Verified by DNS."><input id="nc-domain" className="input" value={f.domain} onChange={set('domain')} placeholder="globex.com" /></Field>
          </div>
          {f.region !== c.installation_region && <div className="banner warn">This installation cannot store data in {c.regions[f.region]}. The region check will fail; onboard them on an installation in that region instead.</div>}
          <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn primary" disabled={!ok1} onClick={() => setStep(2)}>Next</button></div>
        </>}
        {step === 2 && <>
          <div className="grid g2">
            <Field label="Order reference"><input id="nc-order" className="input" value={f.contract.order_ref} onChange={setC('order_ref')} /></Field>
            <Field label="Signed on"><input id="nc-signed" className="input" type="date" value={f.contract.signed_at} onChange={setC('signed_at')} /></Field>
            <Field label="Customer owner (name)"><input id="nc-owner" className="input" value={f.contract.owner_name} onChange={setC('owner_name')} /></Field>
            <Field label="Customer owner (email)"><input id="nc-owner-email" className="input" type="email" value={f.contract.owner_email} onChange={setC('owner_email')} /></Field>
            <Field label="Security contact for emergencies" help="Name and phone."><input id="nc-sec" className="input" value={f.contract.security_contact} onChange={setC('security_contact')} /></Field>
            <Field label="Start date"><input id="nc-start" className="input" type="date" value={f.contract.start_date} onChange={setC('start_date')} /></Field>
            <Field label="Billing contact"><input id="nc-bill" className="input" value={f.contract.billing_contact} onChange={setC('billing_contact')} /></Field>
            <Field label="Purchase order number"><input id="nc-po" className="input" value={f.contract.po_number} onChange={setC('po_number')} /></Field>
          </div>
          <label className="row"><input type="checkbox" checked={f.contract.pilot} onChange={setC('pilot')} /> This is a pilot</label>
          {f.contract.pilot && <Field label="Pilot pass marks" help="Plain words, for example: 3 proven fixes and a weekly board report within 30 days."><textarea id="nc-pass" className="input" value={f.contract.pass_marks} onChange={setC('pass_marks')} /></Field>}
          <div className="row between"><button className="btn" onClick={() => setStep(1)}>Back</button><button className="btn primary" disabled={!ok2} onClick={() => setStep(3)}>Next</button></div>
        </>}
        {step === 3 && <>
          <div className="grid g3">
            <Field label="Keep raw log lines (days)"><input id="nc-ret-logs" className="input" type="number" value={f.retention_log_days} onChange={set('retention_log_days')} /></Field>
            <Field label="Keep run records (days)"><input id="nc-ret-runs" className="input" type="number" value={f.retention_run_days} onChange={set('retention_run_days')} /></Field>
            <Field label="Keep audit trail (days)" help="At least 365."><input id="nc-ret-audit" className="input" type="number" value={f.retention_audit_days} onChange={set('retention_audit_days')} /></Field>
          </div>
          <div className="row wrap" style={{ gap: 18 }}>
            <label className="row"><input type="checkbox" checked={f.mfa_required} onChange={set('mfa_required')} /> Two-step sign-in required</label>
            <label className="row"><input type="checkbox" checked={f.sso_required} onChange={set('sso_required')} /> Single sign-on required</label>
          </div>
          <Field label="AI providers allowed by the data processing agreement">
            <div className="row wrap" style={{ gap: 14 }}>{Object.entries(c.providers).map(([k, v]) => (
              <label key={k} className="row"><input type="checkbox" checked={f.ai_policy.providers.includes(k)} onChange={() => setF({ ...f, ai_policy: { ...f.ai_policy, providers: toggle(f.ai_policy.providers, k) } })} /> {v}</label>))}</div>
          </Field>
          <label className="row"><input type="checkbox" checked={f.ai_policy.zero_retention} onChange={(e) => setF({ ...f, ai_policy: { ...f.ai_policy, zero_retention: e.target.checked } })} /> Zero data retention required from AI providers</label>
          <Field label="Contracted WhiteHats" help="Only available WhiteHats can be sold.">
            <div className="grid g2">{c.whitehats.map((w) => (
              <label key={w.key} className="row" style={{ opacity: w.available ? 1 : 0.5, alignItems: 'flex-start' }} title={w.unavailable_reason}>
                <input type="checkbox" disabled={!w.available} checked={f.contracted_agents.includes(w.key)} onChange={() => setF({ ...f, contracted_agents: toggle(f.contracted_agents, w.key) })} />
                <span><b>{w.name}</b><div className="faint" style={{ fontSize: 12 }}>{w.available ? w.domain : w.unavailable_reason}</div></span></label>))}</div>
          </Field>
          <div className="row between"><button className="btn" onClick={() => setStep(2)}>Back</button><button className="btn primary" disabled={busy === 'c' || f.ai_policy.providers.length === 0} onClick={create}>{busy === 'c' ? <span className="spin" /> : 'Create company'}</button></div>
        </>}
      </div></Card>
    </div>
  )
}

export function CompanyDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const { data, error, loading, reload, setData } = useApi(`/api/operator/tenants/${id}`, { poll: 30000 })
  const [tab, setTab] = useState('checklist')
  const [busy, run] = useAction()
  const [modal, setModal] = useState(null)
  const [link, setLink] = useState(null)
  if (loading && !data) return <Loading rows={8} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const s = data.settings
  const ended = ['offboarded', 'deleted'].includes(s.status)
  const check = (c, action, note = '') => run(`c${c.key}`, () => api(`/api/operator/tenants/${id}/checks/${c.key}`, { method: 'POST', body: { action, note } }), 'Recorded').then((r) => r && setData({ ...data, readiness: r }))
  const supportOn = s.support_access_until && new Date(s.support_access_until + 'Z') > new Date()
  const actions = (c) => {
    if (ended) return null
    if (c.kind === 'manual') {
      return c.status === 'passed' ? <button className="btn small ghost" onClick={() => check(c, 'reset')}>Undo</button>
        : <button className="btn small primary" disabled={busy === `c${c.key}`} onClick={() => check(c, 'pass', 'Confirmed by operator')}>Mark done</button>
    }
    if (c.status === 'failed' && c.key !== 'signoff') return <button className="btn small ghost" onClick={() => setModal({ waive: c })}>Waive</button>
    if (c.status === 'waived') return <button className="btn small ghost" onClick={() => check(c, 'reset')}>Un-waive</button>
    return null
  }
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Link to="/" className="muted">← Companies</Link>
      <div className="page-head">
        <div><div className="row"><Status s={s.status} /><span className="pill neutral">{s.plan}</span><span className="pill neutral">{s.region_label}</span></div>
          <h2 style={{ marginTop: 6 }}>{s.name}</h2><p className="mono" style={{ fontSize: 12 }}>{s.slug} · created {fmt.date(s.created_at)} by {s.created_by}{s.live_at && ` · live since ${fmt.date(s.live_at)}`}</p></div>
        {!ended && <div className="row wrap">
          <button className="btn" disabled={busy === 're'} onClick={() => run('re', () => api(`/api/operator/tenants/${id}/recheck`, { method: 'POST' }), 'All checks re-run').then((r) => r && setData({ ...data, readiness: r }))}>{busy === 're' ? <span className="spin" /> : 'Re-check'}</button>
          <button className="btn" onClick={() => setModal('invite')}>Invite first admin</button>
          {supportOn && <button className="btn" onClick={() => run('sup', () => api(`/api/operator/tenants/${id}/support-session`, { method: 'POST' })).then((r) => { if (r) { startSupport(r.token); window.location.assign('/') } })}>Open support session</button>}
          {s.status !== 'live' && <button className="btn primary" disabled={!data.readiness.ready_for_signoff || !!s.go_live_operator} onClick={() => run('gl', () => api(`/api/operator/tenants/${id}/go-live`, { method: 'POST' }), (d) => d.settings.status === 'live' ? 'Company is live' : 'Signed off. Waiting for the customer.').then((d) => d && setData(d))}>
            {s.go_live_operator ? 'You signed off' : 'Sign off go-live'}</button>}
        </div>}
      </div>
      {s.status === 'suspended' && <div className="banner warn"><div><b>Suspended:</b> {s.suspended_reason}</div></div>}
      {s.status === 'deleted' && <div className="banner info"><div><b>Data deleted.</b> The deletion certificate is stored on this record and in the platform audit trail.</div></div>}
      {link && <Card pad><CodeBlock title="Invitation link (shown once, expires in 72 hours)" text={link.link} />{link.emailed && <p className="muted" style={{ marginTop: 6 }}>{link.emailed}</p>}</Card>}
      <Card pad><ReadinessBar readiness={data.readiness} />
        <div className="row wrap" style={{ marginTop: 10, gap: 14, fontSize: 12.5 }}><span>Operator sign-off: <b>{s.go_live_operator || 'pending'}</b></span><span>Customer sign-off: <b>{s.go_live_customer || 'pending'}</b></span>
          <span>Support access: <b>{supportOn ? `granted until ${fmt.time(s.support_access_until)}` : 'off'}</b></span></div></Card>
      <Tabs tabs={[['checklist', 'Checklist'], ['value', 'First value and health'], ['people', 'Invitations and people'], ['settings', 'Settings'], ['danger', 'Danger zone']]} value={tab} onChange={setTab} />
      {tab === 'checklist' && <Checklist readiness={data.readiness} actions={actions} />}
      {tab === 'value' && <div className="split"><Milestones items={data.first_value} />
        <Card title="Health" pad><dl className="kv">
          <dt>Collectors online</dt><dd>{data.health.collectors_online} of {data.health.collectors_total}{data.health.collectors_silent ? ` · ${data.health.collectors_silent} silent` : ''}</dd>
          <dt>AI calls, 24 hours</dt><dd>{data.health.ai_calls_24h} · {data.health.ai_failures_24h} failed · {data.health.ai_fallbacks_24h} answered by the rules engine</dd>
          <dt>Open critical incidents</dt><dd>{data.health.open_critical_incidents}</dd>
          <dt>Last sign-in</dt><dd>{fmt.ago(data.health.last_login)}</dd>
          <dt>Usage</dt><dd>{data.health.usage.seats}/{data.health.limits.seats} seats · {data.health.usage.collectors}/{data.health.limits.collectors} collectors</dd>
        </dl><p className="faint" style={{ fontSize: 12, marginTop: 10 }}>Operators see counts only. Content needs a support session granted by the customer.</p></Card></div>}
      {tab === 'people' && <div className="grid g2">
        <Card title="Invitations">{data.invites.length === 0 ? <Empty title="None yet" action={!ended && <button className="btn primary" onClick={() => setModal('invite')}>Invite first admin</button>} /> : data.invites.map((i) => (
          <div key={i.id} className="list-item row between"><div><b>{i.email}</b><div className="faint" style={{ fontSize: 12 }}>{i.role.toUpperCase()} · {i.accepted_at ? `accepted ${fmt.ago(i.accepted_at)}` : i.revoked_at ? 'revoked' : i.expired ? 'expired' : `expires ${fmt.time(i.expires_at)}`}</div></div>
            {!i.accepted_at && !i.revoked_at && <button className="btn small ghost" onClick={() => run('rv', () => api(`/api/operator/tenants/${id}/invites/${i.id}/revoke`, { method: 'POST' }), 'Invitation revoked').then(reload)}>Revoke</button>}</div>))}</Card>
        <Card title="People who can sign in">{data.users.length === 0 ? <Empty title="Nobody yet" /> : data.users.map((u) => (
          <div key={u.email} className="list-item row between"><div><b>{u.name}</b><div className="faint" style={{ fontSize: 12 }}>{u.email} · {u.role}</div></div>
            <div className="row">{u.mfa ? <span className="pill good">two-step on</span> : <span className="pill warn">no two-step</span>}{!u.active && <span className="pill neutral">inactive</span>}</div></div>))}</Card>
      </div>}
      {tab === 'settings' && <SettingsTab s={s} id={id} disabled={ended} onSaved={(d) => { setData(d); toast('Settings saved') }} />}
      {tab === 'danger' && <DangerTab s={s} id={id} onDone={() => reload()} onDeleted={() => nav('/')} />}
      {modal === 'invite' && <InviteModal id={id} domain={s.domain} owner={s.contract} onClose={() => setModal(null)} onDone={(l) => { setModal(null); setLink(l); reload() }} />}
      {modal?.waive && <WaiveModal check={modal.waive} onClose={() => setModal(null)} onWaive={(note) => { check(modal.waive, 'waive', note); setModal(null) }} />}
    </div>
  )
}

function InviteModal({ id, domain, owner, onClose, onDone }) {
  const [f, setF] = useState({ email: owner.owner_email || '', name: owner.owner_name || '', role: 'ciso' })
  const [busy, run] = useAction()
  return (
    <Modal title="Invite the first admin" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!!busy} onClick={() => run('i', () => api(`/api/operator/tenants/${id}/invites`, { method: 'POST', body: f }), 'Invitation created').then((d) => d && onDone(d))}>Create invitation</button></>}>
      <div className="stack">
        <Field label="Email" help={domain ? `Must be an address at ${domain}.` : ''}><input id="inv-email" className="input" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Name"><input id="inv-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Role"><Select id="inv-role" value={f.role} onChange={(role) => setF({ ...f, role })} options={ROLES.filter((role) => role.value === 'ceo' || role.value === 'ciso')} /></Field>
        <p className="muted" style={{ fontSize: 12.5 }}>The link works once and expires after 72 hours. WhiteHats never sees their password.</p>
      </div>
    </Modal>
  )
}

function WaiveModal({ check, onClose, onWaive }) {
  const [note, setNote] = useState('')
  return (
    <Modal title={`Waive: ${check.title}`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn danger" disabled={note.trim().length < 10} onClick={() => onWaive(note)}>Waive with this reason</button></>}>
      <Field label="Why is it safe to skip this?" help="At least 10 characters. Recorded in both audit trails."><textarea id="waive-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
    </Modal>
  )
}

function SettingsTab({ s, id, disabled, onSaved }) {
  const [f, setF] = useState({ plan: s.plan, domain: s.domain, mfa_required: s.mfa_required, sso_required: s.sso_required,
    retention_log_days: s.retention.logs, retention_run_days: s.retention.runs, retention_audit_days: s.retention.audit, contract: s.contract })
  const [busy, run] = useAction()
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'number' ? Number(e.target.value) : e.target.value })
  const contract = Object.fromEntries(['order_ref', 'signed_at', 'start_date', 'owner_name', 'owner_email', 'security_contact', 'pilot', 'pass_marks', 'billing_contact', 'po_number', 'timezone', 'work_start', 'work_end'].map((k) => [k, f.contract?.[k] ?? (k === 'pilot' ? false : '')]))
  return (
    <Card><div className="card-body stack" style={{ gap: 14 }}>
      <div className="grid g3">
        <Field label="Plan"><Select id="op-plan" value={f.plan} onChange={(plan) => setF({ ...f, plan })} disabled={disabled} options={PLANS} /></Field>
        <Field label="Email domain" help="Changing it resets verification."><input className="input" value={f.domain} onChange={set('domain')} disabled={disabled} /></Field>
        <Field label="DNS record"><div className="mono" style={{ fontSize: 12, paddingTop: 8, wordBreak: 'break-all' }}>{s.domain_record}</div></Field>
        <Field label="Keep raw logs (days)"><input className="input" type="number" value={f.retention_log_days} onChange={set('retention_log_days')} disabled={disabled} /></Field>
        <Field label="Keep runs (days)"><input className="input" type="number" value={f.retention_run_days} onChange={set('retention_run_days')} disabled={disabled} /></Field>
        <Field label="Keep audit (days)"><input className="input" type="number" value={f.retention_audit_days} onChange={set('retention_audit_days')} disabled={disabled} /></Field>
      </div>
      <div className="row wrap" style={{ gap: 18 }}><label className="row"><input type="checkbox" checked={f.mfa_required} onChange={set('mfa_required')} disabled={disabled} /> Two-step sign-in required</label>
        <label className="row"><input type="checkbox" checked={f.sso_required} onChange={set('sso_required')} disabled={disabled} /> Single sign-on required</label></div>
      <dl className="kv"><dt>AI policy</dt><dd>{s.ai_providers_label || 'not agreed'}{s.ai_policy.zero_retention ? ' · zero retention' : ''} (change only with a new data processing agreement)</dd>
        <dt>Contracted WhiteHats</dt><dd>{s.contracted_names.join(', ') || 'none'}</dd>
        <dt>Pass marks</dt><dd className="row wrap"><span>{contract.pass_marks || '—'}</span><SampleTag origin={s.contract_origin} /></dd></dl>
      {!disabled && <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn primary" disabled={!!busy} onClick={() => run('s', () => api(`/api/operator/tenants/${id}`, { method: 'PUT', body: { ...f, contract } })).then((d) => d && onSaved(d))}>Save settings</button></div>}
    </div></Card>
  )
}

function DangerTab({ s, id, onDone, onDeleted }) {
  const [reason, setReason] = useState('')
  const [name, setName] = useState('')
  const [early, setEarly] = useState(false)
  const [result, setResult] = useState(null)
  const [busy, run] = useAction()
  return (
    <div className="grid g2">
      {['live', 'onboarding', 'draft', 'suspended'].includes(s.status) && <Card title={s.status === 'suspended' ? 'Resume' : 'Suspend'} pad>
        {s.status === 'suspended' ? <button className="btn" onClick={() => run('r', () => api(`/api/operator/tenants/${id}/resume`, { method: 'POST' }), 'Resumed').then(onDone)}>Resume company</button> : <div className="stack">
          <p className="muted" style={{ fontSize: 13 }}>Blocks sign-in and collectors until resumed. Nothing is deleted.</p>
          <Field label="Reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Unpaid invoice since 1 November" /></Field>
          <button className="btn danger" disabled={reason.length < 5 || !!busy} onClick={() => run('s', () => api(`/api/operator/tenants/${id}/suspend`, { method: 'POST', body: { reason } }), 'Suspended').then(onDone)}>Suspend</button></div>}
      </Card>}
      <Card title={s.status === 'offboarded' ? 'Delete data' : 'Offboard'} pad>
        {result ? <CodeBlock title="Result (keep this)" text={JSON.stringify(result, null, 2)} /> : s.status === 'deleted' ? <p className="muted">Already deleted.</p> : <div className="stack">
          <p className="muted" style={{ fontSize: 13 }}>{s.status === 'offboarded' ? `Deletes every row this company owns and issues a certificate. Data is kept until ${s.deletion_due}.` : 'Exports all data, signs everyone out, stops collectors and sets a deletion date 30 days out.'}</p>
          <Field label={`Type "${s.name}" to confirm`}><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          {s.status === 'offboarded' && <label className="row"><input type="checkbox" checked={early} onChange={(e) => setEarly(e.target.checked)} /> Delete early (customer asked in writing)</label>}
          <button className="btn danger" disabled={name !== s.name || !!busy} onClick={() => run('o', () => api(`/api/operator/tenants/${id}/${s.status === 'offboarded' ? 'delete' : 'offboard'}`, { method: 'POST', body: s.status === 'offboarded' ? { confirm_name: name, early } : { confirm_name: name } }),
            s.status === 'offboarded' ? 'Data deleted' : 'Offboarded and exported').then((r) => { if (r) { setResult(r); onDone() } })}>{s.status === 'offboarded' ? 'Delete data' : 'Offboard company'}</button></div>}
      </Card>
      {s.status === 'deleted' && <button className="btn" onClick={onDeleted}>Back to companies</button>}
    </div>
  )
}

export function OperatorAudit() {
  const { data, error, reload } = useApi('/api/operator/audit')
  const toast = useToast()
  const [busy, run] = useAction()
  if (error) return <ErrorNote error={error} retry={reload} />
  if (!data) return <Loading />
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head"><div><h2>Platform audit</h2><p>Every operator action across every company. Each one is also in that company's own audit trail.</p></div>
        <button className="btn" disabled={!!busy} onClick={() => run('ret', () => api('/api/operator/maintenance/retention', { method: 'POST' })).then((r) => { if (r) { toast(`Retention ran: ${Object.values(r.removed).reduce((a, b) => a + b, 0)} rows removed`); reload() } })}>Run retention now</button></div>
      <Card>{data.length === 0 ? <Empty title="Nothing yet" /> : (
        <div className="tbl-wrap"><table className="t"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Company</th><th>Detail</th></tr></thead>
          <tbody>{data.map((a, i) => <tr key={i}><td className="num" style={{ whiteSpace: 'nowrap' }}>{fmt.time(a.at)}</td><td>{a.actor}</td><td><b>{a.action}</b></td><td>{a.target}</td>
            <td className="muted" style={{ fontSize: 12.5, maxWidth: 420, wordBreak: 'break-word' }}>{a.detail}</td></tr>)}</tbody></table></div>)}</Card>
      <button className="btn ghost" style={{ alignSelf: 'flex-start' }} onClick={async () => toast((await copyText(JSON.stringify(data, null, 2))) ? 'Copied as JSON' : 'Copy failed')}>Copy as JSON</button>
    </div>
  )
}
