import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Avatar, Bar, Card, CodeBlock, Empty, ErrorNote, Field, Icon, Loading, Modal, Select, Status, Tabs, useAction } from '../components/ui'
import { ROLES, accessLines, roleLabel } from '../lib/choices'

export default function Settings() {
  const [tab, setTab] = useState('plan')
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="row wrap">
        <Link className="btn small" to="/connectors">Connections</Link>
        <Link className="btn small" to="/setup">Runner</Link>
        <Link className="btn small" to="/models">AI models</Link>
      </div>
      <Tabs tabs={[['plan', 'Plan and usage'], ['users', 'People'], ['audit', 'Audit trail']]} value={tab} onChange={setTab} />
      {tab === 'plan' && <Plan />}{tab === 'users' && <Users />}{tab === 'audit' && <Audit />}
    </div>
  )
}

function Plan() {
  const { user } = useAuth()
  const { data, error, reload } = useApi('/api/admin/plan')
  const [busy, run] = useAction()
  if (error) return <ErrorNote error={error} retry={reload} />
  if (!data) return <Loading />
  const cur = data.plans[data.current]
  const meters = [['Seats', data.usage.seats, cur.seats], ['Collectors', data.usage.collectors, cur.collectors], ['AI tokens this month', data.usage.ai_tokens, cur.ai_tokens], ['Proven fixes', data.usage.fixes, cur.fixes]]
  return (
    <>
      <Card title={`You are on ${cur.label}`} hint={cur.price} pad>
        <div className="grid g4">{meters.map(([l, u, max]) => (
          <div key={l}><div className="row between" style={{ fontSize: 12.5 }}><span className="muted">{l}</span><b className="num">{fmt.n(u)} / {fmt.n(max)}</b></div>
            <Bar value={(100 * u) / max} tone={u / max > 0.9 ? 'crit' : u / max > 0.7 ? 'warn' : ''} /></div>))}</div>
      </Card>
      <div className="grid g3">{Object.entries(data.plans).map(([k, p]) => (
        <div key={k} className={`card plan ${k === data.current ? 'current' : ''}`}>
          <div className="row between"><b style={{ fontSize: 17 }}>{p.label}</b>{k === data.current && <span className="pill brand">Current</span>}</div>
          <div style={{ fontSize: 22, fontWeight: 800 }}>{p.price}</div>
          <ul>{p.features.map((x) => <li key={x}>{x}</li>)}</ul>
          {k !== data.current && allowed(user, 'manage_billing') && <button className="btn" disabled={busy === k} onClick={() => run(k, () => api('/api/admin/plan', { method: 'PUT', body: { plan: k } }), (r) => r.message)}>Request {p.label}</button>}
        </div>))}</div>
    </>
  )
}

function Users() {
  const { user } = useAuth()
  const { data, error, reload } = useApi('/api/admin/users')
  const invites = useApi('/api/admin/invites')
  const [invite, setInvite] = useState(null)
  const [link, setLink] = useState(null)
  const [roleChange, setRoleChange] = useState(null)
  const [busy, run] = useAction()
  if (error) return <ErrorNote error={error} retry={reload} />
  if (!data) return <Loading />
  const admin = allowed(user, 'manage_users')
  const patch = (u, body, msg) => run(`u${u.id}`, () => api(`/api/admin/users/${u.id}`, { method: 'PATCH', body }), msg)
    .then((out) => { if (out) reload(); return out })
  const owners = data.filter((person) => person.active && person.account_role === 'account_owner')
  const roleOptions = (person) => {
    // A CEO owns the account through that title; moving the last one to another title would leave no owner.
    const last = person.role === 'ceo' && person.account_role === 'account_owner' && owners.length === 1
    return ROLES.map((role) => ({
      ...role,
      disabled: last && role.value !== 'ceo',
      disabledReason: 'This is the last account owner. Make someone else an account owner first.',
    }))
  }
  const pending = (invites.data || []).filter((i) => !i.accepted_at && !i.revoked_at && !i.expired)
  return (
    <div className="stack" style={{ gap: 16 }}>
      {link && <Card pad><CodeBlock title="Invitation link (shown once, expires in 72 hours)" text={link.link} />
        <p className="muted" style={{ marginTop: 6, fontSize: 12.5 }}>{link.emailed || 'Send it to them privately. They choose their own password and set up two-step sign-in.'}</p></Card>}
      <Card title="People who can sign in" action={admin && <button className="btn small primary" onClick={() => setInvite({ name: '', email: '', role: 'analyst' })}><Icon.plus size={14} /> Invite someone</button>}>
        <div className="tbl-wrap tbl-stack"><table className="t"><thead><tr><th>Name</th><th>Access</th><th>Status</th><th>Last sign-in</th>{admin && <th />}</tr></thead>
          <tbody>{data.map((u) => (
            <tr key={u.id}><td data-label="Name"><div className="person-identity"><Avatar name={u.name} email={u.email} />
              <div><b>{u.name}</b><div className="muted" style={{ fontSize: 12 }}>{u.email}{u.title && ` · ${u.title}`}</div></div></div></td>
              <td data-label="Access"><div className="row" style={{ gap: 8 }}><Access person={u} />{admin && u.id !== user.id && <button className="btn small" id={`role-${u.id}`} onClick={() => setRoleChange({ person: u, next: u.role })}>Change role</button>}</div></td>
              <td data-label="Status"><Status s={u.active ? 'active' : 'stopped'} /></td><td data-label="Last sign-in" className="num">{fmt.ago(u.last_login)}</td>
              {admin && <td>{u.id !== user.id && <button className="btn small" disabled={busy === `u${u.id}`} onClick={() => patch(u, { active: !u.active }, u.active ? 'Deactivated' : 'Reactivated')}>{u.active ? 'Deactivate' : 'Reactivate'}</button>}</td>}</tr>))}</tbody></table></div>
      </Card>
      {pending.length > 0 && <Card title="Open invitations">{pending.map((i) => (
        <div key={i.id} className="list-item row between"><div><b>{i.email}</b><div className="faint" style={{ fontSize: 12 }}>{roleLabel(i.role)} · invited by {i.created_by} · expires {fmt.time(i.expires_at)}</div></div>
          {admin && <button className="btn small ghost" disabled={busy === `r${i.id}`} onClick={() => run(`r${i.id}`, () => api(`/api/admin/invites/${i.id}/revoke`, { method: 'POST' }), 'Invitation revoked').then(invites.reload)}>Revoke</button>}</div>))}</Card>}
      {invite && <Modal title="Invite someone" onClose={() => setInvite(null)} footer={<><button className="btn" onClick={() => setInvite(null)}>Cancel</button>
        <button className="btn primary" disabled={busy === 'inv' || !invite.email.includes('@')} onClick={() => run('inv', () => api('/api/admin/invites', { method: 'POST', body: invite }), 'Invitation created').then((r) => { if (r) { setInvite(null); setLink(r); invites.reload() } })}>Create invitation</button></>}>
        <div className="stack">
          <div className="grid g2"><Field label="Name"><input id="u-name" className="input" value={invite.name} onChange={(e) => setInvite({ ...invite, name: e.target.value })} /></Field>
            <Field label="Work email"><input id="u-email" className="input" type="email" value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} /></Field></div>
          <Field label="Role"><Select id="u-role" value={invite.role} onChange={(role) => setInvite({ ...invite, role })} options={ROLES} /></Field>
          <p className="muted" style={{ fontSize: 12.5 }}>They get a one-time link that expires in 72 hours. They choose their own password; nobody else ever sees it.</p>
        </div></Modal>}
      {roleChange && <RoleChange person={roleChange.person} next={roleChange.next} options={roleOptions(roleChange.person)} busy={busy === `u${roleChange.person.id}`}
        onNext={(next) => setRoleChange({ ...roleChange, next })} onClose={() => setRoleChange(null)}
        onConfirm={() => patch(roleChange.person, { role: roleChange.next }, 'Role updated').then((out) => { if (out) setRoleChange(null) })} />}
    </div>
  )
}

function Access({ person }) {
  const [first, ...rest] = accessLines(person)
  return <div className="access">{first}{rest.map((line) => <div key={line} className="muted">{line}</div>)}</div>
}

function RoleChange({ person, next, options, busy, onNext, onClose, onConfirm }) {
  const chosen = ROLES.find((role) => role.value === next)
  const same = next === person.role
  return (
    <Modal title={`Change ${person.name}'s role`} onClose={onClose} footer={<><button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy || same || chosen?.disabled} onClick={onConfirm}>Change role</button></>}>
      <div className="stack">
        <p>{person.name} is {roleLabel(person.role)} today.</p>
        <Field label="New role"><Select id={`role-next-${person.id}`} value={next} onChange={onNext} options={options} /></Field>
        {!same && chosen && <p>Change {person.name} from {roleLabel(person.role)} to {chosen.label}? {chosen.description}</p>}
      </div>
    </Modal>
  )
}

function Audit() {
  const { data, error, reload } = useApi('/api/admin/audit')
  if (error) return <ErrorNote error={error} retry={reload} />
  if (!data) return <Loading />
  return (
    <Card title="Audit trail" hint="Every decision and change, with who made it">
      {data.length === 0 ? <Empty title="Nothing recorded yet" /> : (
        <div className="tbl-wrap"><table className="t"><thead><tr><th>When</th><th>Who</th><th>What</th><th>On</th><th>Detail</th></tr></thead>
          <tbody>{data.map((a, i) => <tr key={i}><td className="num" style={{ whiteSpace: 'nowrap' }}>{fmt.time(a.at)}</td><td>{a.actor}</td><td><b>{a.action}</b></td><td>{a.target}</td><td className="muted" style={{ fontSize: 12.5, maxWidth: 360, wordBreak: 'break-word' }}>{a.detail}</td></tr>)}</tbody></table></div>
      )}
    </Card>
  )
}
