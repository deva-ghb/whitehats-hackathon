import { useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import '../studio/studio.css'
import '../studio/scene/scene.css'
import { api, fmt, getProjectSlug, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Empty, ErrorNote, Icon, Loading, Modal, useAction, useToast } from '../components/ui'
import { NewCloneDialog } from '../studio/connect'
import { blankClone } from '../studio/edit'
import { FOUND, Workspace, useMedia } from '../studio/Workspace'


export default function Studio() {
  return <ReactFlowProvider><StudioScreen /></ReactFlowProvider>
}

function StudioScreen() {
  const { user, org, support } = useAuth()
  // STUDIO.7: unless the installation turns drawing by hand on, the connections draw the map and the Studio only shows it.
  const handEditing = org?.studio_hand_editing !== false
  const clones = useApi('/api/clones')
  const library = useApi('/api/library/types')
  const found = useApi('/api/discoveries')
  const [params, setParams] = useSearchParams()
  const [cloneId, setCloneId] = useState(() => params.get('clone'))
  // An old Connectors link, and GitHub's return after the install, ask for one connection's node to be selected.
  const connection = params.get('connection')
  const notice = params.get('github') === 'not-linked'
    ? <div className="banner crit slim studio-found" role="alert"><div>GitHub was not connected: {params.get('reason') || 'the install did not finish.'}</div></div> : null
  const [creating, setCreating] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const foundDoc = found.data?.document
  const hasFound = Boolean(foundDoc && (foundDoc.nodes.length || foundDoc.groups.length))
  const list = [...(clones.data?.clones || []),
    ...(hasFound ? [{ id: FOUND, clone: 'Found by connectors', environment: 'shared', version: null }] : [])]
  const home = org?.local_workspace && list.find(item => item.clone === `${getProjectSlug()}-estate`)
  const current = cloneId ?? (home || list[0])?.id ?? null
  const showingFound = current === FOUND
  const saved = useApi(current && !showingFound ? `/api/clones/${current}` : null)
  const clone = showingFound ? { data: { id: FOUND, document: foundDoc }, loading: found.loading, error: found.error, reload: found.reload } : saved
  const [busy, run] = useAction()
  const toast = useToast()
  const file = useRef(null)
  const phone = useMedia('(max-width: 767px)')
  const writer = allowed(user, 'edit_model')
  const editing = writer && handEditing && !phone && !showingFound
  // Putting a connection on the map is the connection drawing, so setting connections up is enough to do it.
  const mayConnect = !handEditing && !support && (writer || allowed(user, 'manage_connections'))

  const importFile = async (event) => {
    const picked = event.target.files?.[0]
    event.target.value = ''
    if (!picked) return
    let body
    try {
      body = JSON.parse(await picked.text())
    } catch {
      toast('That file is not a clone document: it is not valid JSON.', 'error')
      return
    }
    const out = await run('import', () => api('/api/clones/import', { method: 'POST', body }), 'Clone imported')
    if (out) { await clones.reload(); setCloneId(out.id) }
  }
  const exportClone = () => run('export', async () => {
    const body = await api(`/api/clones/${current}/export`)
    const link = Object.assign(window.document.createElement('a'), {
      href: URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: 'application/json' })),
      download: `${body.clone}.clone.json`,
    })
    link.click()
    setTimeout(() => URL.revokeObjectURL(link.href), 1000)
  })
  const startClone = (slug, environment) => run('create', async () => {
    const author = user?.name || 'Studio'
    const body = blankClone({ slug, environment, libraryVersion: library.data?.library_version, author })
    const out = await api('/api/clones', { method: 'POST', body: { reason: 'Started a blank clone', document: body } })
    await clones.reload()
    setCloneId(out.id)
    setCreating(false)
  }, 'Clone created')

  const connect = (key) => run('connect', async () => {
    const out = await api(`/api/connections/${key}/place`, { method: 'POST' })
    await clones.reload()
    setCloneId(out.clone)
    await saved.reload()  // the new connection is in the map before it is selected (current may be the default, unnamed)
    setParams((now) => { const next = new URLSearchParams(now); next.set('connection', key); return next }, { replace: true })
    setConnecting(false)
  })
  const connectDialog = connecting && <ConnectSourceDialog busy={busy === 'connect'} onPick={connect} onClose={() => setConnecting(false)} />

  const importButton = writer && handEditing && (
    <>
      <input ref={file} type="file" accept="application/json,.json" hidden onChange={importFile} data-testid="clone-file" />
      <button className="btn small" disabled={busy === 'import'} onClick={() => file.current?.click()}>
        {busy === 'import' ? <span className="spin" /> : <Icon.upload size={14} />}<span className="btn-label">Import a clone</span></button>
    </>
  )
  const estateLink = writer && handEditing && (
    <Link className="btn small" to={`/p/${getProjectSlug()}/studio/import`}><Icon.layers size={14} /><span className="btn-label">Import an estate</span></Link>
  )
  const dialog = creating && (
    <NewCloneDialog libraryVersion={library.data?.library_version} busy={busy} onCreate={startClone} onClose={() => setCreating(false)} />
  )

  if (clones.loading && !clones.data) return <div className="studio"><Loading rows={6} /></div>
  if (clones.error) return <ErrorNote error={clones.error} retry={clones.reload} />
  if (!list.length && !handEditing) {
    return (
      <div className="studio studio-empty">
        <Empty title="No map yet" action={mayConnect ? <button className="btn small primary" onClick={() => setConnecting(true)}><Icon.plug size={14} />Connect a source</button> : null}>
          WhiteHats draws a map of your estate from your connections: services, data, networks and who can reach what.
          {mayConnect ? ' Connect AWS or GitHub and the map appears here.' : ' Ask an engineer or an account admin to connect AWS or GitHub.'}
        </Empty>
        {connectDialog}
      </div>
    )
  }
  if (!list.length) {
    return (
      <div className="studio studio-empty">
        <Empty title="No clone yet" action={editing ? <>{importButton}{estateLink}<button className="btn small primary" onClick={() => setCreating(true)}>Start a blank clone</button></> : (importButton || null)}>
          A clone is a map of your estate: services, data, networks and who can reach what.
          {writer ? ' Import an estate from Terraform state or from what your connections found, import a clone document, or start a blank one.' : ' Ask an engineer or your security lead to import one.'}
        </Empty>
        {dialog}
      </div>
    )
  }
  return (
    <>
      <Workspace clone={clone} library={library} list={list} current={current} onPickClone={setCloneId}
        marks={found.data?.marks} onMarksChange={found.reload} select={connection} notice={notice}
        found={showingFound ? found.data : null} foundNote={showingFound ? <FoundNote found={found.data} /> : null}
        importButton={<>{estateLink}{importButton}</>} onExport={showingFound ? null : exportClone} exporting={busy === 'export'} editing={editing}
        author={user?.name || 'Studio'} onNew={editing ? () => setCreating(true) : undefined}
        handEditing={handEditing} onConnect={mayConnect ? () => setConnecting(true) : undefined} />
      {dialog}
      {connectDialog}
    </>
  )
}

const SOURCES = [
  { key: 'aws', name: 'AWS', about: 'Your services, data stores, queues and roles, read through a read-only role.' },
  { key: 'github', name: 'GitHub', about: 'Your repositories, what they build and deploy, and the pipelines that ship them.' },
  { key: 'cloudwatch', name: 'CloudWatch logs', about: 'The activity and application logs WhiteHats reads every five minutes.' },
]

/** Choose a source: its node goes on the map (starting the map if there is none) and its setup opens beside it. */
function ConnectSourceDialog({ busy, onPick, onClose }) {
  const [picked, setPicked] = useState(null)
  return (
    <Modal title="Connect a source" onClose={onClose} footer={<button className="btn" onClick={onClose}>Cancel</button>}>
      <p className="muted" style={{ margin: '0 0 12px' }}>WhiteHats draws the map from what the source can see, and keeps it up to date.</p>
      <ul className="stack connect-sources" style={{ gap: 8, listStyle: 'none', padding: 0, margin: 0 }}>
        {SOURCES.map((source) => (
          <li key={source.key}>
            <button className="btn connect-source" disabled={busy} aria-busy={busy && picked === source.key}
              onClick={() => { setPicked(source.key); onPick(source.key) }}>
              <b>{source.name}</b><span className="muted">{source.about}</span>
            </button>
          </li>))}
      </ul>
    </Modal>
  )
}

/** What the connections found, when, and what may be out of date. The view is read only. */
function FoundNote({ found }) {
  const missing = found.sources.filter((source) => source.missing.length || source.last_error)
  const marked = Object.keys(found.marks).length
  return (
    <div className={`banner slim studio-found ${missing.length ? 'warn' : 'info'}`} role="status">
      <div>
        Read only: what your connections found. {found.sources.map((source) => `${source.name} found ${source.nodes} ${source.nodes === 1 ? 'resource' : 'resources'} ${fmt.ago(source.discovered_at)}.`).join(' ')}
        {' '}Generic nodes need a type before they can go into a clone; Import an estate lets you choose one.
        {missing.map((source) => (
          <div key={source.id} className="studio-found-problem">
            {marked} {marked === 1 ? 'node may be' : 'nodes may be'} out of date: {source.missing.length ? `${source.name} is missing ${source.missing.join(', ')}.` : `${source.name} last read failed.`}
          </div>))}
      </div>
    </div>
  )
}
