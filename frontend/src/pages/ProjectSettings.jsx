import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, useApi } from '../lib/api'
import { useAuth } from '../App'
import { Avatar, Card, Empty, ErrorNote, Field, Loading, Modal, Select, useAction } from '../components/ui'
import { PROJECT_ROLES as ROLES, PROJECT_ROLE_LABEL as ROLE_LABEL } from '../lib/choices'

const CRITICALITY = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]
// The server asks every grant and removal to say why, in at least this many characters, for the audit trail.
const REASON_MIN = 5
const NO_MEMBER = { email: '', role: 'viewer', reason: '' }

export default function ProjectSettings() {
  const { slug } = useParams()
  const navigate = useNavigate()
  const { user, org } = useAuth()
  const { data, error, loading, reload } = useApi(`/api/projects/${slug}/settings`)
  const [form, setForm] = useState(null)
  const [member, setMember] = useState(NO_MEMBER)
  const [removing, setRemoving] = useState(null)
  const [removeReason, setRemoveReason] = useState('')
  const [arm, setArm] = useState(false)
  const [busy, run] = useAction()

  useEffect(() => {
    if (!data) return
    setForm({
      name: data.name, criticality: data.criticality,
      environments: (data.environments || []).join(', '), two_approver: data.two_approver, weekly_audits: data.weekly_audits,
    })
  }, [data])

  if (error) return <ErrorNote error={error} retry={reload} />
  if ((loading && !data) || !form) return <Loading rows={5} />
  const can = data.can || {}
  const save = () => run('save', async () => {
    if (can.manage_projects) {
      await api(`/api/projects/${slug}`, {
        method: 'PATCH',
        body: {
          name: form.name, criticality: form.criticality,
          environments: form.environments.split(',').map((item) => item.trim()).filter(Boolean),
        },
      })
    }
    if (can.set_policies) {
      // only what changed, so the audit trail records real changes, not every save
      const changed = Object.fromEntries(['two_approver', 'weekly_audits'].filter((k) => !!form[k] !== !!data[k]).map((k) => [k, !!form[k]]))
      if (Object.keys(changed).length) await api(`/api/projects/${slug}/policy`, { method: 'PATCH', body: changed })
    }
    return {}
  }, 'Saved').then(reload)
  const invite = () => run('invite', () => api(`/api/projects/${slug}/members`, {
    method: 'POST', body: member,
  }), 'Invited').then((out) => { if (out) setMember(NO_MEMBER); reload() })
  const closeRemove = () => { setRemoving(null); setRemoveReason('') }
  const remove = () => run('remove', () => api(`/api/projects/${slug}/members`, {
    method: 'DELETE', body: { email: removing.email, reason: removeReason },
  }), 'Removed from this project').then((out) => { if (out) closeRemove(); reload() })
  const archive = () => run('archive', async () => {
    if (can.archive_project) {
      await api(`/api/projects/${slug}/archive`, { method: 'POST', body: { confirm: true } })
    } else {
      await api(`/api/projects/${slug}`, { method: 'PATCH', body: { archived: true } })
    }
    return {}
  }, 'Archived').then(() => navigate('/'))

  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card title="Project" pad>
        <div className="stack" style={{ gap: 12 }}>
          <Field label="Name">
            <input id="settings-name" className="input" value={form.name} disabled={!can.manage_projects}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          {!can.manage_projects && <p className="muted">Only an Account owner or Account admin can rename this project.</p>}
          <Field label="Criticality">
            <Select id="settings-criticality" value={form.criticality} disabled={!can.manage_projects}
              onChange={(criticality) => setForm({ ...form, criticality })} options={CRITICALITY} />
          </Field>
          <Field label="Environments" help="Comma-separated. For example: production, staging.">
            <input id="settings-environments" className="input" value={form.environments} disabled={!can.manage_projects}
              onChange={(e) => setForm({ ...form, environments: e.target.value })} />
          </Field>
          <label className="row">
            <input id="settings-two-approver" type="checkbox" checked={!!form.two_approver} disabled={!can.set_policies}
              onChange={(e) => setForm({ ...form, two_approver: e.target.checked })} />
            <span>Two different Approvers must approve a critical fix</span>
          </label>
          <label className="row">
            <input id="settings-weekly-audits" type="checkbox" checked={!!form.weekly_audits} disabled={!can.set_policies}
              onChange={(e) => setForm({ ...form, weekly_audits: e.target.checked })} />
            <span>Run a full security audit every week (7 days after the last one). New connections start one either way.</span>
          </label>
          {!can.set_policies && <p className="muted">Only a Project admin or a Security lead can change that.</p>}
          {(can.manage_projects || can.set_policies) && (
            <button className="btn primary" disabled={busy === 'save'} onClick={save}>
              {busy === 'save' ? <span className="spin" /> : 'Save'}
            </button>
          )}
        </div>
      </Card>
      <Card title="Connections" pad>
        {data.connections.length === 0 ? <Empty title="No connection is attached" /> : (
          <div className="tbl-wrap"><table className="t"><thead><tr><th>Connection</th><th>Kind</th><th>Scope</th></tr></thead>
            <tbody>{data.connections.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td>{row.category}</td>
                <td>{scopeText(row.scope)}</td>
              </tr>
            ))}</tbody></table></div>
        )}
      </Card>
      {org?.local_workspace && <Card title="AI settings" pad>
        <Link className="btn" to={`/p/${slug}/models`}>Models and cost control</Link>
      </Card>}
      <Card title="People" pad>
        {data.members.length === 0 ? <Empty title="No one is in this project yet" /> : (
          <div className="tbl-wrap tbl-stack"><table className="t"><thead><tr><th>Person</th><th>Role</th>{can.manage_members && <th>Access</th>}</tr></thead>
            <tbody>{data.members.map((row) => (
              <tr key={row.email}><td data-label="Person"><div className="person-identity"><Avatar name={row.name} email={org?.local_workspace ? row.email : undefined} />
              <div><b>{row.name}</b><div className="muted">{row.email}</div></div></div></td><td data-label="Role">{ROLE_LABEL[row.role] || row.role}</td>
                {can.manage_members && <td data-label="Access">{row.email === user?.email
                  ? <span className="faint" style={{ fontSize: 12.5 }}>Nobody changes their own role</span>
                  : <button className="btn small" onClick={() => setRemoving(row)} aria-label={`Remove ${row.name} from this project`}>Remove</button>}</td>}
              </tr>
            ))}</tbody></table></div>
        )}
        {can.manage_members && (
          <div className="stack" style={{ gap: 12, marginTop: 16 }}>
            <div className="grid g2">
              <Field label="Work email">
                <input id="member-email" className="input" type="email" value={member.email}
                  onChange={(e) => setMember({ ...member, email: e.target.value })} />
              </Field>
              <Field label="Project role">
                <Select id="member-role" value={member.role} onChange={(role) => setMember({ ...member, role })} options={ROLES} />
              </Field>
            </div>
            <Field label="Why" help="Goes on the audit trail with your name. For example: joins the on-call rota.">
              <input id="member-reason" className="input" value={member.reason} maxLength={300}
                onChange={(e) => setMember({ ...member, reason: e.target.value })} />
            </Field>
            <button className="btn" disabled={busy === 'invite' || !member.email.trim() || member.reason.trim().length < REASON_MIN} onClick={invite}>
              {busy === 'invite' ? <span className="spin" /> : 'Invite to this project'}
            </button>
          </div>
        )}
        {!can.manage_members && <p className="muted">Only a Project admin or an Account admin can invite someone to this project.</p>}
      </Card>
      {removing && (
        <Modal title={`Remove ${removing.name} from this project`} onClose={closeRemove} footer={<>
          <button className="btn" onClick={closeRemove}>Cancel</button>
          <button className="btn danger" disabled={busy === 'remove' || removeReason.trim().length < REASON_MIN} onClick={remove}>
            {busy === 'remove' ? <span className="spin" /> : 'Remove from project'}
          </button>
        </>}>
          <div className="stack" style={{ gap: 12 }}>
            <p className="muted">They keep their account and any other projects. This goes on the audit trail with your name.</p>
            <Field label="Why">
              <input id="remove-reason" className="input" value={removeReason} maxLength={300} autoFocus
                onChange={(e) => setRemoveReason(e.target.value)} />
            </Field>
          </div>
        </Modal>
      )}
      {(can.archive_project || can.manage_projects) && (
        <Card title="Archive" pad>
          <p>Archiving hides the project. What it already holds stays.</p>
          {arm ? (
            <button className="btn danger" disabled={busy === 'archive'} onClick={archive}>
              {busy === 'archive' ? <span className="spin" /> : 'Archive now'}
            </button>
          ) : <button className="btn" onClick={() => setArm(true)}>Archive this project</button>}
        </Card>
      )}
    </div>
  )
}

const SCOPE_NAMES = { accounts: 'Accounts', regions: 'Regions', resource_groups: 'Resource groups', repos: 'Repositories', pipelines: 'Pipelines' }

/** A scope in words: what it narrows the connection to, or everything it reads. */
function scopeText(scope) {
  const parts = Object.entries(scope || {}).map(([key, value]) => (key === 'tags'
    ? `Tags ${Object.entries(value).map(([name, wanted]) => `${name}=${wanted}`).join(', ')}`
    : `${SCOPE_NAMES[key] || key}: ${value.join(', ')}`))
  return parts.length ? parts.join('; ') : 'Everything it reads'
}
