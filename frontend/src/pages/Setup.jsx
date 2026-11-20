import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import Checklist, { Milestones, ReadinessBar } from '../components/Checklist'
import { Card, CodeBlock, ErrorNote, Field, Loading, Modal, SampleTag, Select, useAction } from '../components/ui'
import { ALERT_CHANNELS, SUPPORT_HOURS, WORK_TIMES, WORK_ZONES } from '../lib/choices'

const NEXT = {
  identity_connector: ['/connectors', 'Connect identity'], cloud_logs: ['/connectors', 'Connect cloud logs'],
  collector_reporting: ['/collectors', 'Deploy a collector'], whitehats: ['/agents', 'Open WhiteHats'], ai_model: ['/models', 'Test a model'],
}

export default function Setup() {
  const { user, reload } = useAuth()
  const { data, error, loading, setData, reload: refresh } = useApi('/api/setup')
  const [busy, run] = useAction()
  const [modal, setModal] = useState(null)
  if (loading && !data) return <Loading rows={8} />
  if (error) return <ErrorNote error={error} retry={refresh} />
  const s = data.settings
  const connections = allowed(user, 'manage_connections')
  const signOff = allowed(user, 'sign_off')
  const recheck = () => run('re', () => api('/api/setup/recheck', { method: 'POST' }), 'Checks re-run').then((d) => d && setData(d))
  const supportOn = s.support_access_until && new Date(s.support_access_until + 'Z') > new Date()

  const actions = (c) => {
    if (c.status === 'passed' || c.status === 'waived') return null
    if (NEXT[c.key]) return <Link className="btn small" to={NEXT[c.key][0]}>{NEXT[c.key][1]}</Link>
    if (c.key === 'domain') return <button className="btn small" onClick={() => setModal('domain')}>Show DNS record</button>
    if (c.key === 'alerts' && connections) return <button className="btn small" onClick={() => setModal('alerts')}>Set up alerts</button>
    if (c.key === 'repos' && connections) return <button className="btn small" onClick={() => setModal('repos')}>Link repositories</button>
    return null
  }

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div><h2>{s.status === 'live' ? 'Your setup' : 'Your checklist to go live'}</h2>
          <p>Each item is checked automatically from your live setup. Items marked "WhiteHats" are handled by our team. Press Re-check after you change something.</p></div>
        <div className="row"><button className="btn" disabled={busy === 're'} onClick={recheck}>{busy === 're' ? <span className="spin" /> : 'Re-check now'}</button>
          {signOff && s.status !== 'live' && <button className="btn primary" disabled={!data.readiness.ready_for_signoff || busy === 'go'} title={data.readiness.ready_for_signoff ? '' : 'Finish the blocking items first'}
            onClick={() => run('go', () => api('/api/setup/go-live', { method: 'POST' }), (r) => r.status === 'live' ? 'You are live' : 'Signed off. Waiting for WhiteHats to sign off too.').then(() => { refresh(); reload() })}>
            {s.go_live_customer ? 'Signed off' : 'Sign off go-live'}</button>}</div>
      </div>
      <Card pad><ReadinessBar readiness={data.readiness} />
        {s.go_live_customer && s.status !== 'live' && <div className="banner info" style={{ marginTop: 12 }}>You signed off as {s.go_live_customer}. WhiteHats signs off next.</div>}
        {data.readiness.blocking.length > 0 && <p className="muted" style={{ marginTop: 10, fontSize: 12.5 }}>Still needed: {data.readiness.blocking.join(' · ')}</p>}</Card>
      <div className="split">
        <Checklist readiness={data.readiness} actions={actions} />
        <div className="stack" style={{ gap: 16 }}>
          <Milestones items={data.first_value} />
          <Card title={<span className="row wrap">Your agreement <SampleTag origin={s.contract_origin} /></span>} pad>
            <dl className="kv">
              <dt>Plan</dt><dd>{s.plan}</dd><dt>Data region</dt><dd>{s.region_label}</dd>
              <dt>Retention</dt><dd>Logs {s.retention.logs} days · runs {s.retention.runs} days · audit {s.retention.audit} days</dd>
              <dt>AI policy</dt><dd>{s.ai_providers_label || 'not agreed'}{s.ai_policy.zero_retention ? ' · zero data retention' : ''}</dd>
              <dt>Two-step sign-in</dt><dd>{s.mfa_required ? 'Required for everyone' : 'Optional'}</dd>
              <dt>Alerts</dt><dd>{s.alerts.kind ? `${s.alerts.kind} ${s.alerts.target}${s.alerts.verified_at ? ' · tested' : ' · not tested'}` : 'Not set'}</dd>
            </dl>
          </Card>
          <WorkingHours contract={s.contract} editable={allowed(user, 'manage_account_setup')} onSaved={setData} />
          <Card title="WhiteHats support access" hint="Our team sees nothing unless you allow it" pad>
            {supportOn ? <div className="stack"><div className="banner warn">Granted until {fmt.time(s.support_access_until)}{s.support_project ? `, in ${s.support_project} only` : ''}: {s.support_access_reason}</div>
              {signOff && <button className="btn danger" onClick={() => run('rv', () => api('/api/setup/support-access', { method: 'DELETE' }), 'Support access revoked').then(refresh)}>Revoke now</button>}</div>
              : <div className="stack"><p className="muted" style={{ fontSize: 13 }}>Off. Grant read-only access for up to 72 hours when you want help; every page they open is recorded in your audit trail.</p>
                {signOff && <button className="btn" onClick={() => setModal('support')}>Grant support access</button>}</div>}
          </Card>
        </div>
      </div>
      {modal === 'domain' && <Modal title="Verify your email domain" onClose={() => setModal(null)} footer={<button className="btn primary" onClick={() => { setModal(null); recheck() }}>I added it, check now</button>}>
        <div className="stack"><p>Add this TXT record to the DNS of <b>{s.domain}</b>. It proves you own the domain, so only your staff can be invited.</p>
          <CodeBlock title="TXT record value" text={s.domain_record} />
          <p className="muted" style={{ fontSize: 12.5 }}>Host or name: <span className="mono">@</span> (the domain itself). Changes can take up to an hour to appear.</p></div></Modal>}
      {modal === 'alerts' && <AlertsModal current={s.alerts} onClose={() => setModal(null)} onDone={() => { setModal(null); refresh() }} />}
      {modal === 'repos' && <ReposModal repos={s.repos} onClose={() => setModal(null)} onDone={() => { setModal(null); refresh() }} />}
      {modal === 'support' && <SupportModal onClose={() => setModal(null)} onDone={() => { setModal(null); refresh() }} />}
    </div>
  )
}

function withCurrent(options, value) {
  if (!value || options.some((opt) => opt.value === value)) return options
  return [{ value, label: value, description: 'Saved for this company.' }, ...options]
}

function WorkingHours({ contract, editable, onSaved }) {
  const savedZone = contract?.timezone || 'UTC'
  const savedStart = contract?.work_start || '09:00'
  const savedEnd = contract?.work_end || '17:00'
  const [zone, setZone] = useState(savedZone)
  const [start, setStart] = useState(savedStart)
  const [end, setEnd] = useState(savedEnd)
  const [busy, run] = useAction()
  const zoneLabel = withCurrent(WORK_ZONES, savedZone).find((opt) => opt.value === savedZone)?.label || savedZone
  const save = () => run('hours', () => api('/api/setup/hours', {
    method: 'PUT', body: { timezone: zone, work_start: start, work_end: end },
  }), 'Working hours saved').then((d) => d && onSaved(d))
  return (
    <Card title="Working hours" hint="Insider uses these when it judges a sign-in" pad>
      <div className="stack">
        <p className="muted" style={{ fontSize: 13 }}>{contract?.timezone
          ? `Insider uses ${savedStart} to ${savedEnd} in ${zoneLabel}.`
          : 'Until you save a change, Insider uses 09:00 to 17:00 UTC.'}</p>
        <Field label="Time zone" help="An event that already has a zone keeps it. This zone is for events that do not.">
          <Select id="work-zone" value={zone} onChange={setZone} options={withCurrent(WORK_ZONES, zone)} disabled={!editable} />
        </Field>
        <div className="row wrap">
          <Field label="Start"><Select id="work-start" value={start} onChange={setStart} options={withCurrent(WORK_TIMES, start)} disabled={!editable} /></Field>
          <Field label="End"><Select id="work-end" value={end} onChange={setEnd} options={withCurrent(WORK_TIMES, end)} disabled={!editable} /></Field>
        </div>
        {editable
          ? <button className="btn primary" disabled={!!busy || start === end} onClick={save}>{busy === 'hours' ? <span className="spin" /> : 'Save working hours'}</button>
          : <p className="faint" style={{ fontSize: 12.5 }}>Every project uses these hours, so only an Account owner or an Account admin can change them.</p>}
      </div>
    </Card>
  )
}

function AlertsModal({ current, onClose, onDone }) {
  const [kind, setKind] = useState(current.kind || 'slack')
  const [target, setTarget] = useState('')
  const [busy, run] = useAction()
  const save = async () => {
    const ok = await run('s', () => api('/api/setup/alerts', { method: 'PUT', body: { kind, target } }))
    if (ok) { const t = await run('t', () => api('/api/setup/alerts/test', { method: 'POST' }), (r) => r.message); if (t) onDone() }
  }
  return (
    <Modal title="Where should alerts go?" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!!busy || target.length < 5} onClick={save}>{busy ? <span className="spin" /> : 'Save and send a test'}</button></>}>
      <div className="stack">
        <Field label="Channel"><Select id="al-kind" value={kind} onChange={setKind} options={ALERT_CHANNELS} /></Field>
        <Field label={kind === 'email' ? 'Email address (use a team list)' : 'Webhook address'} help={<Link to="/help/connectors/alerts">How to create one</Link>}>
          <input id="al-target" className="input" value={target} onChange={(e) => setTarget(e.target.value)} placeholder={kind === 'email' ? 'security@company.com' : 'https://hooks.slack.com/services/…'} /></Field>
        <p className="muted" style={{ fontSize: 12.5 }}>Critical and high signals are sent here as they happen. The check passes once the test arrives.</p>
      </div>
    </Modal>
  )
}

function ReposModal({ repos, onClose, onDone }) {
  const [text, setText] = useState(repos.map((r) => `${r.name}, ${r.url}, ${r.test}`).join('\n'))
  const [busy, run] = useAction()
  const save = () => {
    const list = text.split('\n').map((l) => l.split(',').map((x) => x.trim())).filter((p) => p[0] && p[1]).map(([name, url, test = '']) => ({ name, url, test }))
    run('r', () => api('/api/setup/repos', { method: 'PUT', body: { repos: list } }), 'Repositories saved').then((d) => d && onDone())
  }
  return (
    <Modal title="Code repositories for Fix Factory" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!!busy} onClick={save}>Save</button></>}>
      <Field label="One per line: name, address, test command" help="Each must be reachable with git ls-remote from the WhiteHats server.">
        <textarea id="repos" className="input mono" style={{ minHeight: 140 }} value={text} onChange={(e) => setText(e.target.value)} placeholder="payments-api, https://github.com/acme/payments-api.git, npm test" /></Field>
    </Modal>
  )
}

function SupportModal({ onClose, onDone }) {
  const [hours, setHours] = useState(4)
  const [reason, setReason] = useState('')
  const [busy, run] = useAction()
  return (
    <Modal title="Grant WhiteHats support access" onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!!busy || reason.length < 5} onClick={() => run('g', () => api('/api/setup/support-access', { method: 'POST', body: { hours: Number(hours), reason } }), 'Support access granted').then((d) => d && onDone())}>Grant</button></>}>
      <div className="stack">
        <Field label="For how long"><Select id="sup-hours" value={hours} onChange={setHours} options={SUPPORT_HOURS} /></Field>
        <Field label="Why" help="Shown to our team and kept in your audit trail."><input id="sup-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Help connecting CloudWatch" /></Field>
        <p className="muted" style={{ fontSize: 12.5 }}>Access is read-only and ends automatically. You can revoke it at any time.</p>
      </div>
    </Modal>
  )
}
