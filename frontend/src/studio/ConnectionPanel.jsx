// What the inspector shows for a connection node (STUDIO.4): how the connection stands, its setup, what this project reads from it,
// and what it found. Setting a connection up is the account's business (manage connections); choosing what a project reads is
// the project's (attach connections); everyone else reads the same words and cannot change them. The server holds every secret.
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { allowed, useAuth } from '../App'
import { ContractStatus, canRead, contractHasNews, hasChecked, hasMissing, lagText, shownStatus, stoppedReading } from '../components/ConnectorParts'
import { Field, Modal, Status, useAction } from '../components/ui'
import { api, fmt, useApi } from '../lib/api'
import { connectorKey, insideConnection } from './connection.js'
import { bringInWords } from './useConnections'

const SCOPE_OF = { aws: ['regions', 'Regions', 'region'], cloudwatch: ['regions', 'Regions', 'region'], github: ['repos', 'Repositories', 'repository'] }

export function ConnectionPanel({ item, connections, document, editing, marks }) {
  const { user } = useAuth()
  const key = connectorKey(item.type)
  const connector = connections.byKey.get(key)
  const [busy, run] = useAction()
  const canSetup = allowed(user, 'manage_connections')
  if (connections.loading) return <p className="muted" role="status">Looking up this connection…</p>
  if (!connector) return <p className="muted">This connection is not available.</p>
  const inside = insideConnection(document, item)
  const problems = inside.filter((node) => marks?.[node.id]).length
  const soon = connector.status === 'coming_soon'
  const connected = !soon && connector.status !== 'available'
  // Bringing in what a connection found is the connection drawing (STUDIO.7): an editor, or whoever sets connections up, may.
  const mayBringIn = editing || (Boolean(document) && (canSetup || allowed(user, 'edit_model')))
  const act = { busy, run, connector, connections, editing, mayBringIn, canSetup, ck: key }
  return (
    <section className="connection-panel stack" aria-label={`${connector.name} connection`} data-connector={key} style={{ gap: 12 }}>
      <div className="row between wrap">
        <b>{connector.name}</b>
        <Status s={shownStatus(connector)} wrap />
      </div>
      <p className="muted" style={{ fontSize: 12.5 }}>{connector.description}</p>
      {soon && <div className="banner info slim connection-soon" role="note"><div><b>Coming soon.</b> {connector.name} can be placed and named on the canvas, but it cannot be connected in this version.</div></div>}
      {!soon && connector.last_check && !hasMissing(connector) && connector.last_check !== connector.sync?.last_error
        && <p className="contract-check" style={{ fontSize: 12.5 }}>{connector.last_check}</p>}
      {connected && contractHasNews(connector.contract) && <ContractStatus contract={connector.contract} />}
      {connector.sync?.last_error && <div className="banner crit slim" role="alert"><div>{connector.sync.last_error}</div></div>}
      {!soon && <Setup {...act} />}
      {connected && SCOPE_OF[key] && <Scope {...act} />}
      {connected && <Reading {...act} />}
      {connected && <Found {...act} inside={inside} problems={problems} />}
      {connected && key === 'aws' && <SettingsCheck settings={connector.sync?.settings} />}
      {!soon && connector.doc && <div><Link className="btn small ghost flush" to={`/help/${connector.doc}`}>Setup guide</Link></div>}
    </section>
  )
}

/** CS.1: the account's live settings, checked by Prowler every hour and again within minutes of a change. */
function SettingsCheck({ settings }) {
  if (!settings?.status) return <p className="muted settings-check" role="status">Its settings are checked within a minute of connecting.</p>
  if (settings.status === 'running') {
    return <p className="settings-check running" role="status"><span className="spin" /> Checking this account&apos;s settings{settings.services?.length ? ` (${settings.services.join(', ')})` : ''}…</p>
  }
  if (settings.status === 'interrupted') {
    return <p className="muted settings-check" role="status">The last settings check was cut off by a server restart. It runs again within a minute.</p>
  }
  if (settings.status === 'limited') {
    return <div className="banner warn slim settings-check" role="note"><div>Settings checked {fmt.ago(settings.finished_at)}, but not all of them: {settings.error}</div></div>
  }
  if (settings.status === 'failed') {
    return <div className="banner warn slim settings-check" role="note"><div>The settings check could not run: {settings.error}</div></div>
  }
  const problems = settings.problems || 0
  return (
    <p className="muted settings-check" role="status">
      Settings checked {fmt.ago(settings.finished_at)}: {problems ? `${problems} serious ${problems === 1 ? 'problem' : 'problems'}, shown on the map` : 'no serious problems'}.
    </p>
  )
}

/** The connection's setup: what is read, with which read-only access. Secrets are written here and never read back. */
function Setup({ connector, connections, busy, run, canSetup, editing, mayBringIn, ck }) {
  const [values, setValues] = useState({ ...connector.config })
  // Setup is open until the connection is set up, then it folds away so how the connection stands comes first.
  const [open, setOpen] = useState(connector.status === 'available')
  useEffect(() => { setValues({ ...connector.config }) }, [connector.id, connector.status]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setOpen(connector.status === 'available') }, [connector.id, connector.status])
  const viaApp = ck === 'github'
  const install = () => run('install', () => api('/api/github/install', { method: 'POST' })).then((out) => { if (out?.url) window.location.assign(out.url) })
  if (viaApp) {
    return connector.status === 'available'
      ? (canSetup && <div><button className="btn small primary" disabled={busy === 'install'} onClick={install}>{busy === 'install' ? <span className="spin" /> : 'Install the GitHub App'}</button>
        <p className="faint" style={{ fontSize: 12, marginTop: 6 }}>GitHub asks which repositories to share. WhiteHats only reads.</p></div>)
      : <p className="faint" style={{ fontSize: 12 }}>Connected by installing the WhiteHats GitHub App.</p>
  }
  if (!canSetup) return <p className="faint" style={{ fontSize: 12 }}>Setting this connection up needs the account owner or an admin. You can see how it stands.</p>
  // Connecting checks the access and, when it works, finds what the connection can see and brings it into the node.
  const save = () => run('save', async () => {
    const saved = await api(`/api/connectors/${connector.id}`, { method: 'PUT', body: { values } })
    connections.reload()  // the access check is answered: say so now, while what the connection can see is still being read
    if (saved.status === 'error' || ck === 'cloudwatch') return { saved }
    await api(`/api/connectors/${connector.id}/discover`, { method: 'POST' })
    return { saved, done: mayBringIn ? await connections.bringIn(ck) : null }
  }, ({ saved, done }) => (done ? `${saved.last_check} ${bringInWords(done)}` : saved.last_check)).then((out) => { connections.reload(); return out })
  return (
    <details className="connection-setup" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Setup</summary>
    <form className="stack" style={{ gap: 10, marginTop: 8 }} aria-label={`Set up ${connector.name}`}
      onSubmit={(event) => { event.preventDefault(); save() }}>
      {connector.fields.map((field) => (
        <Field key={field.key} label={`${field.label}${field.required ? '' : ' (optional)'}`}
          help={field.secret && connector.has_secret ? 'Stored. Leave blank to keep it.' : undefined}>
          <input id={`connection-${field.key}`} className="input" type={field.secret ? 'password' : 'text'} autoComplete="off"
            value={values[field.key] || ''} onChange={(event) => setValues({ ...values, [field.key]: event.target.value })} />
        </Field>))}
      <div><button className="btn small primary" type="submit" disabled={busy === 'save'}>
        {busy === 'save' ? <span className="spin" /> : connector.status === 'available' ? 'Connect' : 'Save and check'}</button></div>
    </form>
    </details>
  )
}

/** What this project reads from the connection: everything, or only the regions or repositories ticked. */
function Scope({ connector, ck, busy, run, editing, mayBringIn, connections }) {
  const { user } = useAuth()
  const [field, noun, one] = SCOPE_OF[ck]
  const choices = useApi(`/api/connections/${ck}/choices`, { deps: [connector.status, connector.last_sync_at] })
  const [ticked, setTicked] = useState(null)
  const saved = choices.data?.scope?.[field]
  useEffect(() => { setTicked(saved ? [...saved] : null) }, [JSON.stringify(saved)]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!choices.data || (choices.data[field]?.length || 0) < 2) return null  // nothing to choose between
  const canAttach = allowed(user, 'attach_connections')
  const all = ticked === null
  const toggle = (name) => setTicked((now) => {
    const base = now === null ? [...choices.data[field]] : now
    return base.includes(name) ? base.filter((x) => x !== name) : [...base, name]
  })
  const save = () => run('scope', async () => {
    await api(`/api/connectors/${connector.id}/scope`, { method: 'PUT', body: { scope: all ? {} : { [field]: ticked } } })
    const done = mayBringIn ? await connections.bringIn(ck) : null
    choices.reload()
    return done
  }, (done) => `This project now reads ${all ? `every ${one}` : `${ticked.length} of ${choices.data[field].length}`}.${done ? ` ${bringInWords(done)}` : ''}`)
  return (
    <fieldset className="connection-scope stack" style={{ gap: 6, border: 0, padding: 0 }}>
      <legend className="inspector-tier" style={{ padding: 0 }}>{noun} this project reads</legend>
      <label className="row" style={{ gap: 8 }}><input type="radio" name={`scope-${ck}`} checked={all} disabled={!canAttach}
        onChange={() => setTicked(null)} /> Everything the connection can see</label>
      <label className="row" style={{ gap: 8 }}><input type="radio" name={`scope-${ck}`} checked={!all} disabled={!canAttach}
        onChange={() => setTicked([...choices.data[field]])} /> Only the ones I choose</label>
      {!all && (
        <ul className="stack" style={{ gap: 4, listStyle: 'none', padding: 0, margin: '2px 0 0' }} aria-label={`Choose ${noun.toLowerCase()}`}>
          {choices.data[field].map((name) => (
            <li key={name}><label className="row" style={{ gap: 8 }}>
              <input type="checkbox" checked={ticked.includes(name)} disabled={!canAttach} onChange={() => toggle(name)} /> <span className="mono">{name}</span>
            </label></li>))}
        </ul>)}
      {canAttach && <div><button className="btn small" disabled={busy === 'scope'} onClick={save}>{busy === 'scope' ? <span className="spin" /> : 'Save what this project reads'}</button></div>}
      {!canAttach && <p className="faint" style={{ fontSize: 12 }}>Choosing what a project reads needs a project admin or an account admin.</p>}
    </fieldset>
  )
}

/** What the connection found, and the actions that read again. */
function Found({ connector, connections, busy, run, canSetup, editing, mayBringIn, ck, inside, problems }) {
  const find = () => run('find', async () => {
    await api(`/api/connectors/${connector.id}/discover`, { method: 'POST' })
    const done = mayBringIn ? await connections.bringIn(ck) : null
    connections.reload()
    return done
  }, (done) => (done ? `${connector.name}: ${bringInWords(done)}` : `${connector.name} looked again. Someone who can edit this clone brings what it found into the node.`))
  const check = () => run('check', () => api(`/api/connectors/${connector.id}/check`, { method: 'POST' }), (r) => r.last_check).then(connections.reload)
  const [asking, setAsking] = useState(false)
  const disconnect = () => run('disconnect', () => api(`/api/connectors/${connector.id}`, { method: 'DELETE' }), 'Disconnected')
    .then(() => { setAsking(false); connections.reload() })
  const found = connector.contract?.discovered_at
  const discoverable = ck !== 'cloudwatch' && Boolean(connector.contract)  // CloudWatch reads log lines on a schedule: it finds no resources
  return (
    <div className="connection-found stack" style={{ gap: 8 }}>
      {discoverable && <p className="faint" style={{ fontSize: 12 }} data-testid="connection-found">
        {inside.length === 0 ? 'Nothing from this connection is in the clone yet.' : `${inside.length} ${inside.length === 1 ? 'thing' : 'things'} found by this connection ${inside.length === 1 ? 'is' : 'are'} inside this node.`}
        {found && <> Last looked {fmt.ago(found)}.</>}
        {problems > 0 && <> {problems} may be out of date, and say why.</>}
      </p>}
      {canSetup && (
        <div className="row wrap">
          {discoverable && <button className="btn small" disabled={busy === 'find'} onClick={find}>{busy === 'find' ? <span className="spin" /> : 'Find resources'}</button>}
          {hasChecked(connector) && <button className="btn small" disabled={busy === 'check'} onClick={check}>{busy === 'check' ? <span className="spin" /> : 'Check again'}</button>}
          <button className="btn small danger" disabled={busy === 'disconnect'} onClick={() => setAsking(true)}>Disconnect</button>
        </div>)}
      {asking && (
        <Modal title={`Disconnect ${connector.name}?`} onClose={() => setAsking(false)} footer={<>
          <button className="btn" onClick={() => setAsking(false)}>Cancel</button>
          <button className="btn danger" disabled={busy === 'disconnect'} onClick={disconnect}>{busy === 'disconnect' ? <span className="spin" /> : `Disconnect ${connector.name}`}</button></>}>
          <div className="stack" style={{ gap: 8 }}>
            <p>WhiteHats stops reading {connector.name}, and forgets the access you gave it.</p>
            <p>Everything this connection brought into your maps leaves them. Anything drawn by hand stays.</p>
            <p className="muted">Findings and incidents already raised stay, as history. You can connect again at any time.</p>
          </div>
        </Modal>)}
    </div>
  )
}

/** Log reading, for a connection that reads lines on a schedule: when it last read, and a way to read again. */
function Reading({ connector, connections, busy, run, canSetup }) {
  const read = () => run('sync', () => api(`/api/connectors/${connector.id}/sync`, { method: 'POST' }), (r) => `Read ${r.read} new lines`).then(connections.reload)
  return (
    <>
      {connector.last_sync_at && (connector.status === 'reading' || connector.status === 'error') && (
        <p className="faint" style={{ fontSize: 12 }}>
          {stoppedReading(connector) ? 'Last good read' : 'Last read'} {fmt.ago(connector.last_sync_at)} · {fmt.n(connector.sync?.total || 0)} items{lagText(connector.contract)}</p>)}
      {connector.status === 'checked' && connector.category === 'Cloud logs' && <p className="faint" style={{ fontSize: 12 }}>Nothing has been read on a schedule yet.</p>}
      {canSetup && canRead(connector) && (
        <div><button className="btn small" disabled={busy === 'sync'} onClick={read}>{busy === 'sync' ? <span className="spin" /> : stoppedReading(connector) ? 'Try again' : 'Read now'}</button></div>)}
    </>
  )
}
