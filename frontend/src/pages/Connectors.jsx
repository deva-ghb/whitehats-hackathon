import { useState } from 'react'
import { Link } from 'react-router-dom'
import { allowed, useAuth } from '../App'
import { Card, Disclosure, ErrorNote, Field, Loading, Modal, Status, useAction } from '../components/ui'
import { api, fmt, getProjectSlug, useApi } from '../lib/api'

export default function Connectors() {
  const { user } = useAuth()
  const { data, loading, error, reload } = useApi('/api/connectors', { poll: 15000 })
  const [editing, setEditing] = useState(null)
  const [values, setValues] = useState({})
  const [busy, run] = useAction()
  if (loading && !data) return <Loading rows={4} />
  if (error) return <ErrorNote error={error} retry={reload} />
  const manage = allowed(user, 'manage_connections')
  const slug = encodeURIComponent(getProjectSlug())
  const studio = '/p/' + slug + '/studio'
  const providerLinks = ['aws', 'cloudwatch', 'github']
  return <div className="stack">
    <div className="page-head"><div><h2>Connectors and coverage</h2>
      <p>Review account-wide provider access and the latest data read.</p></div></div>
    <div className="grid g2">
      {data.map(connector => <Card key={connector.id} title={connector.name} action={<Status s={connector.status} />} pad>
        <div className="stack">
          <p>{connector.description}</p>
          <p className="muted">Last scheduled read {connector.last_sync_at ? fmt.time(connector.last_sync_at) : 'pending'}</p>
          {connector.last_check && <p>{connector.last_check}</p>}
          {connector.key === 'gcp' && <GoogleCloud connector={connector} slug={slug} />}
          {connector.contract?.permissions?.length > 0 && (
            <Disclosure title={`Permissions (${connector.contract.permissions.length})`}>
              <ul className="connector-permissions">
                {connector.contract.permissions.map(permission => (
                  <li key={permission.name}><span>{permission.name}</span>
                    <span className="muted">{permission.ok === null ? 'Unchecked' : permission.ok ? 'Granted' : 'Missing'}</span></li>
                ))}
              </ul>
            </Disclosure>
          )}
          <div className="row wrap">
            {providerLinks.includes(connector.key) && <Link className="btn" to={studio + '?connection=' + connector.key}>Open in Digital Twin</Link>}
            {manage && (connector.local_access || !providerLinks.includes(connector.key)) && connector.status !== 'coming_soon' &&
              <button className="btn" onClick={() => { setValues(connector.config); setEditing(connector) }}>Configure</button>}
            {manage && connector.key === 'gcp' && ['checked', 'reading'].includes(connector.status) &&
              <button className="btn" disabled={Boolean(busy)} onClick={() =>
                run('sync', () => api('/api/connectors/' + connector.id + '/sync', { method: 'POST' }), 'Read complete').then(reload)}>Read now</button>}
            {manage && connector.key === 'gcp_logging' && ['checked', 'reading'].includes(connector.status) &&
              <button className="btn" disabled={Boolean(busy)} onClick={() =>
                run('sync', () => api('/api/connectors/' + connector.id + '/sync', { method: 'POST' }), 'Read complete').then(reload)}>Read logs</button>}
          </div>
        </div>
      </Card>)}
    </div>
    {editing && <Modal title={'Connect ' + editing.name} onClose={() => setEditing(null)}>
      <form className="stack" onSubmit={async (event) => {
        event.preventDefault()
        const saved = await run('connect', () => api('/api/connectors/' + editing.id, {method:'PUT', body:{ values }}))
        if (saved) { setEditing(null); await reload() }
      }}>
        {editing.fields.filter(field => !editing.local_access || !field.secret).map(field => <Field key={field.key} label={field.label}>
          {field.secret && editing.key === 'gcp_logging'
            ? <textarea className="input" aria-label={field.label} value={values[field.key] || ''} onChange={e => setValues({ ...values, [field.key]: e.target.value })} />
            : <input className="input" aria-label={field.label} type={field.secret ? 'password' : 'text'} value={values[field.key] || ''}
              onChange={e => setValues({ ...values, [field.key]: e.target.value })} />}
        </Field>)}
        <button className="btn primary" disabled={Boolean(busy)}>Save and check access</button>
      </form>
    </Modal>}
  </div>
}

function GoogleCloud({ connector, slug }) {
  const sync = connector.sync || {}
  const types = Object.entries(sync.by_type || {})
  if (connector.status === 'available') return <p className="muted">
    Create the read-only reader first: run <code>docs/connectors/gcp-readonly-setup.sh &lt;project-id&gt; &lt;whitehats-identity&gt;</code>,
    then configure with the project ID and the reader email it prints. No key is created or stored.</p>
  return <div className="stack">
    {sync.last_error && <p><span className="pill error">Last read failed</span> {sync.last_error}</p>}
    {sync.checked_at && <div className="row wrap">
      <span className="pill info">{sync.resources ?? 0} resources read</span>
      <span className={'pill ' + (sync.problems ? 'critical' : 'neutral')}>{sync.problems ?? 0} settings problems open</span>
      <span className={'pill ' + (sync.changes ? 'high' : 'neutral')}>{sync.changes ?? 0} risky changes in the last read</span>
    </div>}
    {types.length > 0 && <p className="muted">{types.map(([type, count]) => `${count} ${type}`).join(' · ')}</p>}
    {sync.checked_at && <p className="muted">Settings checked and activity read {fmt.time(sync.checked_at)}. Reads again every minute.
      {' '}<Link to={'/p/' + slug + '/incidents'}>See the findings</Link></p>}
  </div>
}
