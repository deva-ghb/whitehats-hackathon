// The Studio workspace: the bar, the library rail, the canvas, the inspector and the problems drawer.
// Editing rules live in editor.js (and edit.js, geometry.js, session.js); the drawing lives in FlatCanvas.jsx.
// This file only puts them side by side.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getProjectSlug, useApi } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Icon, Select, useToast } from '../components/ui'
import { FlatCanvas } from './FlatCanvas'
import { TwinGraph } from './TwinGraph'
import { IsoCanvas } from './scene/IsoCanvas'
import { PlanIcon, SceneIcon } from './scene/viewicons'
import { ConnectDialog, GroupDialog } from './connect'
import { useEditor } from './editor'
import { HistoryDialog } from './history'
import { EditIcon } from './icons'
import { typeIndex } from './model'
import { Inspector, LibraryRail, ProblemsDrawer } from './panels'
import { useDocumentEdits } from './session'
import { SensePanel } from './sensepanel'
import { AccountAlerts } from './accountalerts'
import { Recommendations } from './recommendations'
import { AuditNotice } from './auditnotice'
import { ShortcutsDialog, useEditorKeys, useHelpKey } from './shortcuts'
import { connectionNode } from './connection'
import { ConnectionsContext } from './connectionContext'
import { useConnections } from './useConnections'
import { useSensePoints } from './useSensePoints'

// Below 1280 px the library and the inspector side by side leave the canvas a strip of about 200 px, so the library becomes a drawer
// and the inspector a sheet (as on a tablet). The sense panel (studio.css) and the fitted view (FlatCanvas.jsx) use the same width.
const NARROW = '(max-width: 1279px)'
const PHONE = '(max-width: 767px)'
// What the project's connections found (CN.5) is shown beside the clones, read only, never saved as a clone.
export const FOUND = 'found'

export function useMedia(query) {
  const [hit, setHit] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const list = window.matchMedia(query)
    const update = () => setHit(list.matches)
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return hit
}

const VIEW_KEY = 'wh-studio-view'
// A person who has only used the keyboard so far gets the plan view, which is built for it (see useStudioView).
let pointerSeen = false
if (typeof window !== 'undefined') window.addEventListener('pointerdown', () => { pointerSeen = true }, { capture: true })

const readChoice = (key, local) => {
  try {
    const value = window.localStorage.getItem(key)
    return value === 'scene' || value === 'plan' || (local && value === 'graph') ? value : null
  } catch {
    return null
  }
}

/**
 * Remember each person's view. The hackathon workspace opens on the relationship graph by default.
 * Other workspaces open on the flat plan for phones and keyboard users, and the isometric scene otherwise.
 */
export function useStudioView(phone, local = false) {
  const key = local ? `${VIEW_KEY}-local-network` : VIEW_KEY
  const [chosen, setChosen] = useState(() => readChoice(key, local))
  const [keyboard, setKeyboard] = useState(false)
  useEffect(() => {
    if (chosen) return undefined
    const onKey = (event) => { if (event.key === 'Tab' && !pointerSeen) setKeyboard(true) }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [chosen])
  const choose = useCallback((next) => {
    setChosen(next)
    try { window.localStorage.setItem(key, next) } catch { /* a private window keeps the choice for this visit only */ }
  }, [key])
  return [chosen || (local ? 'graph' : phone || keyboard ? 'plan' : 'scene'), choose]
}

export function Workspace({ clone, library, list, current, onPickClone, found, foundNote, importButton, onExport, exporting, editing, author, onNew, marks: allMarks, onMarksChange, select, notice, handEditing = true, onConnect }) {
  const narrow = useMedia(NARROW)
  const phone = useMedia(PHONE)
  const toast = useToast()
  const { user, org, support } = useAuth()
  const surface = useRef(null)
  const boxes = useRef(new Map())
  const dragRef = useRef(null)
  const [railOpen, setRailOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [status, setStatus] = useState({ laying: false, count: 0 })
  const local = Boolean(org?.local_workspace)
  const [chosenView, chooseView] = useStudioView(phone, local)
  // STUDIO.7: while the connections draw the map, the Studio is the scene alone; the plan is the view for drawing by hand.
  const view = editing && chosenView === 'graph' ? 'plan' : (handEditing || local ? chosenView : 'scene')
  const serverMatches = clone.data && String(clone.data.id) === String(current)
  const edits = useDocumentEdits({ server: serverMatches ? clone.data : null, current, author, enabled: editing })
  // The draft stays on screen when the viewport drops to a phone: editing turns off, the drawing does not.
  const cloneDoc = edits.draft || (serverMatches ? clone.data.document : null)
  const types = useMemo(() => typeIndex(library.data?.types), [library.data])
  const ed = useEditor({ edits, document: cloneDoc, library, types, surface, boxes, editing })
  const { sel, setSel, ids } = ed
  const sense = useSensePoints(serverMatches && current !== FOUND ? current : null)
  // SC.1: each service node's known package weaknesses, ranked by reach, for the version on screen.
  const weak = useApi(serverMatches && current !== FOUND ? `/api/clones/${current}/weaknesses` : null,
    { deps: [clone.data?.document?.version?.number] })
  // Open cloud alerts on the buildings they concern, asked for again every 15 seconds while the Studio is open.
  const alerts = useApi(serverMatches && current !== FOUND ? `/api/clones/${current}/alerts` : null, { poll: 15_000, deps: [current] })
  const alerted = useMemo(() => new Map(Object.entries(alerts.data?.nodes || {}).map(([id, list]) => [id, list.map((a) => a.title).join('; ')])),
    [alerts.data])
  const picked = ids.length === 1 ? cloneDoc?.nodes.find((node) => node.id === ids[0]) : null
  const marks = allMarks || found?.marks
  const connections = useConnections({ cloneId: current, edits, enabled: Boolean(cloneDoc) && current !== FOUND, after: onMarksChange, landed: () => (surface.current?.fitWhenDrawn ? surface.current.fitWhenDrawn() : surface.current?.fit()) })
  const canEdit = allowed(user, 'edit_model') && !support

  // Asked to select a connection's node (an old link): select it once the clone is open, or say it is not there.
  const asked = useRef(null)
  // A connection just placed may not be in the map this screen holds yet: look again with each newer version, and only
  // say it is missing when no newer version brought it within a few seconds.
  useEffect(() => {
    if (!select || !cloneDoc || current === FOUND) return undefined
    const key = `${current}|${select}`
    if (asked.current === key) return undefined
    const node = connectionNode(cloneDoc, select)
    if (node) {
      asked.current = key
      setSel({ kind: 'item', ids: [node.id] })
      surface.current?.reveal(node.id)
      return undefined
    }
    const later = window.setTimeout(() => {
      asked.current = key
      toast(`This map has no ${select === 'aws' ? 'AWS' : select === 'github' ? 'GitHub' : select.replaceAll('_', ' ')} connection yet. Use Connect a source to add it.`, 'info')
    }, 4000)
    return () => window.clearTimeout(later)
  }, [select, cloneDoc, current, setSel, toast])

  useEditorKeys({ editing, ids, actions: ed.keyActions, onScreen: view === 'scene' })
  useHelpKey(useCallback(() => setHelpOpen(true), []))
  useEffect(() => { setRailOpen(false); setDrawerOpen(false) }, [current])
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (window.document.querySelector('[role="dialog"]')) return
      if (railOpen) setRailOpen(false)
      else setSel(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [railOpen, setSel])

  const restore = async (number) => {
    try {
      const old = await api(`/api/clones/${current}/versions/${number}`)
      edits.apply(() => old, `Restored version ${number}`)
      setSel(null)
      setHistoryOpen(false)
    } catch (error) {
      toast(error.message || 'That version could not be opened.', 'error')
    }
  }

  const readOnly = !editing && !foundNote && (!phone || !handEditing)
  const readOnlyWhy = local && !support ? "Select a building block or relationship to inspect its details." : support
    ? 'This is a support session. It can look at the clone and export it, but never change it.'
    : !handEditing
      ? 'WhiteHats draws this map from your connections and keeps it up to date. Corrections by hand come in a later release.'
      : `You are signed in as ${user?.title || user?.role}. Changing the clone needs the Engineer or Project admin role in this project, so ask one of them to make the change. You can still look around, search the library and export it.`
  const rail = handEditing && (!narrow || railOpen)
  return (
    <ConnectionsContext.Provider value={connections}>
    <div className={`studio ${narrow ? 'narrow' : ''} ${editing ? 'editing' : ''} ${foundNote || notice || readOnly ? 'with-found' : ''} ${handEditing ? '' : 'no-rail'}`}>
      <StudioBar list={list} current={current} onPickClone={onPickClone} cloneDoc={cloneDoc} count={status.count} laying={status.laying}
        onRelayout={() => (editing ? ed.relayoutEdit() : surface.current?.relayout())} onExport={onExport} exporting={exporting}
        importButton={importButton} narrow={narrow} phone={phone} onFit={() => surface.current?.fit()} railOpen={railOpen} onToggleRail={() => setRailOpen((open) => !open)}
        onNew={onNew} saveState={editing ? edits.saveState : null} editing={editing} edits={edits} ed={ed}
        onHistory={() => setHistoryOpen(true)} onHelp={() => setHelpOpen(true)} showHistory={Boolean(cloneDoc) && !foundNote}
        handEditing={handEditing} onConnect={onConnect} />
      {phone && handEditing && <div className="banner info slim studio-phone" role="status"><div>On a phone the Studio is view only. Editing needs a wider screen.</div></div>}
      {foundNote}
      {notice}
      {readOnly && <div className="banner info slim studio-found studio-readonly" role="status"><div><b>Read only.</b> {readOnlyWhy}</div></div>}
      {rail && <LibraryRail library={library} canPlace={editing} picked={sel?.kind === 'type' ? sel.type.id : null}
        onClose={narrow ? () => setRailOpen(false) : undefined}
        onDragType={(type) => { dragRef.current = type }}
        onDragEnd={() => { window.setTimeout(() => { dragRef.current = null }, 0) }}
        onPlace={(type) => { ed.place(type); if (narrow) setRailOpen(false) }}
        onPick={(type) => { setSel({ kind: 'type', type }); if (narrow) setRailOpen(false) }} />}
      {narrow && rail && <div className="studio-scrim" aria-hidden="true" onClick={() => setRailOpen(false)} />}
      {handEditing || local ? <ViewSwitch view={view} onChoose={chooseView} showGraph={local && !editing} /> : <SearchSoon />}
      {(() => {
        const Surface = view === 'graph' ? TwinGraph : view === 'scene' ? IsoCanvas : FlatCanvas
        return (
          <Surface key={view} ref={surface} document={cloneDoc} current={current} loading={clone.loading} error={clone.error} retry={clone.reload}
            editing={editing} selectedIds={ids} selectedEdge={sel?.kind === 'edge' ? sel : null} describeItem={ed.describeItem} types={types}
            events={ed.surfaceEvents} marks={marks} dragRef={dragRef} onStatus={setStatus} sensePoints={sense.points}
            weaknesses={weak.data?.nodes} findings={alerted} showRelations={local}
            emptyHint={editing ? 'Drag a type from the library onto the canvas, or select one and press Enter to place it.' : 'It has no nodes or boundaries yet.'} />
        )
      })()}
      <AuditNotice />
      {cloneDoc && (
        <div className="studio-corner">
          {!editing && <Recommendations />}
          {current !== FOUND && <AccountAlerts data={alerts.data} />}
        </div>
      )}
      {cloneDoc && current !== FOUND && (
        <SensePanel sense={sense} editing={editing} selectedNode={picked} shownVersion={cloneDoc.version?.number}
          onPick={(id) => { setSel({ kind: 'item', ids: [id] }); surface.current?.reveal(id) }} />
      )}
      {(!narrow || ed.selection) && <Inspector selection={ed.selection} names={ed.names} editing={editing} marks={marks} onClose={() => setSel(null)} actions={ed.inspector}
        connections={connections} document={cloneDoc} weaknesses={weak.data?.nodes} alerts={alerts.data?.nodes} attribution={weak.data?.attribution}
        imageAttribution={weak.data?.image_attribution} handEditing={handEditing}
        onPickRelated={local ? (id) => { setSel({ kind: 'item', ids: [id] }); surface.current?.reveal(id) } : undefined} />}
      <ProblemsDrawer problems={ed.issues} open={drawerOpen} onToggle={() => setDrawerOpen((open) => !open)} onPick={(id) => {
        setSel({ kind: 'item', ids: [id] })
        surface.current?.reveal(id)
      }} />
      {ed.pending && cloneDoc && <ConnectDialog document={cloneDoc} types={types} fromId={ed.pending.fromId} toId={ed.pending.toId} names={ed.names}
        onAdd={ed.addConnection} onClose={() => ed.setPending(null)} />}
      {ed.grouping && <GroupDialog types={library.data?.types || []} count={ids.length} onGroup={ed.group} onClose={() => ed.setGrouping(false)} />}
      {helpOpen && <ShortcutsDialog canEdit={canEdit} onClose={() => setHelpOpen(false)} />}
      {historyOpen && <HistoryDialog cloneId={current} canRestore={editing} onRestore={restore} onClose={() => setHistoryOpen(false)} />}
    </div>
    </ConnectionsContext.Provider>
  )
}

/** Where finding and correcting things by hand will be, over the top right corner of the canvas (STUDIO.7). */
function SearchSoon() {
  return (
    <div className="studio-search-soon" title="Finding things on the map, and correcting them by hand, come in a later release.">
      <Icon.search size={14} />
      <input className="input" aria-label="Search the map (coming soon)" placeholder="Search the map" disabled />
      <span className="pill neutral">Coming soon</span>
    </div>
  )
}

/** Available views of the clone, above the canvas. */
function ViewSwitch({ view, onChoose, showGraph }) {
  return (
    <div className="studio-view" role="group" aria-label="View">
      {showGraph && <button className={`btn small ${view === 'graph' ? 'on' : ''}`} aria-pressed={view === 'graph'}
        aria-label="Graph view" onClick={() => onChoose('graph')}>Graph</button>}
      <button className={`btn small ${view === 'scene' ? 'on' : ''}`} aria-pressed={view === 'scene'} aria-label="Scene view" title="Isometric scene, like the website" onClick={() => onChoose('scene')}>
        <SceneIcon size={16} /><span>Scene</span></button>
      <button className={`btn small ${view === 'plan' ? 'on' : ''}`} aria-pressed={view === 'plan'} aria-label="Plan view" title="Flat plan, for exact editing and for the keyboard" onClick={() => onChoose('plan')}>
        <PlanIcon size={16} /><span>Plan</span></button>
    </div>
  )
}

// STUDIO.6: what a connection no longer finds, or found with no library type, waits for a person in an open import.
const WAITING = 'Waiting for you'

function WaitingLink({ cloneId }) {
  const { data } = useApi(`/api/imports?clone_id=${cloneId}`, { poll: 60_000, deps: [cloneId] })
  const waiting = (data?.imports || []).filter((set) => set.status === 'open' && set.label.startsWith(WAITING) && set.undecided > 0)
  if (!waiting.length) return null
  const count = waiting.reduce((sum, set) => sum + set.undecided, 0)
  return (
    <Link className="btn small studio-waiting" to={`/p/${getProjectSlug()}/studio/import?set=${waiting[0].id}`}
      title="What your connections no longer find, or found without a type, waits here for you to confirm or keep">
      <Icon.alert size={14} /><span className="btn-label">{WAITING}</span><span className="pill warn">{count}</span>
    </Link>
  )
}

function StudioBar({
  list, current, onPickClone, cloneDoc, count, laying, onRelayout, onExport, exporting, importButton, narrow, phone, railOpen, onToggleRail,
  onNew, saveState, editing, edits, ed, onHistory, onHelp, showHistory, onFit, handEditing, onConnect,
}) {
  const version = cloneDoc?.version
  const options = list.map((entry) => ({ value: entry.id, label: entry.clone,
    description: entry.id === FOUND ? 'read only, from your connections' : `${entry.environment}, version ${entry.version}` }))
  const saveLabel = saveState === 'saving' ? 'Saving' : saveState === 'error' ? 'Not saved' : 'Saved'
  return (
    <div className="studio-bar" role="toolbar" aria-label="Clone">
      {narrow && handEditing && <button className="btn small" aria-expanded={railOpen} onClick={onToggleRail}><Icon.book size={14} /><span className="btn-label">Library</span></button>}
      <div className="studio-clone">
        {options.length > 1
          ? <Select id="studio-clone" label="Clone" value={current} onChange={onPickClone} options={options} />
          : <b>{list[0].clone}</b>}
      </div>
      {cloneDoc && <span className="pill neutral">{cloneDoc.environment}</span>}
      {version && current !== FOUND && <span className="faint studio-version">Version {version.number}, {version.author}</span>}
      {saveState && <span className={`studio-save ${saveState === 'error' ? 'bad' : 'faint'}`} data-testid="save-state" role="status">{saveLabel}</span>}
      <div className="spacer" />
      {editing && (
        <div className="bar-group" role="group" aria-label="Edit">
          <button className="btn small icon-only" title="Undo (Ctrl+Z)" aria-label="Undo last change" disabled={!edits.canUndo} onClick={edits.undo}><EditIcon.undo size={16} /></button>
          <button className="btn small icon-only" title="Redo (Ctrl+Shift+Z)" aria-label="Redo" disabled={!edits.canRedo} onClick={edits.redo}><EditIcon.redo size={16} /></button>
          <button className={`btn small icon-only desktop-only ${ed.snapOn ? 'on' : ''}`} title="Snap to the grid" aria-label="Snap to the grid" aria-pressed={ed.snapOn}
            onClick={() => ed.setSnapOn((on) => !on)}><EditIcon.grid size={16} /></button>
        </div>
      )}
      {showHistory && <button className="btn small icon-only wide-only" title="Version history" aria-label="Version history" onClick={onHistory}><EditIcon.history size={16} /></button>}
      <button className="btn small icon-only desktop-only" title="Keyboard and mouse (?)" aria-label="Keyboard and mouse help" onClick={onHelp}><EditIcon.keyboard size={16} /></button>
      {onNew && <button className="btn small primary" onClick={onNew}><Icon.plus size={14} /><span className="btn-label">New clone</span></button>}
      {!handEditing && cloneDoc && current !== FOUND && <WaitingLink cloneId={current} />}
      {onConnect && <button className="btn small primary" onClick={onConnect}><Icon.plug size={14} /><span className="btn-label">Connect a source</span></button>}
      <button className="btn small icon-only" title="Fit to screen" aria-label="Fit to screen" onClick={onFit} disabled={!count}><Icon.fit size={16} /></button>
      <button className="btn small icon-only" title="Lay out again" aria-label="Lay out again" onClick={onRelayout} disabled={laying || !cloneDoc}><Icon.refresh size={16} /></button>
      {onExport && <button className="btn small" onClick={onExport} disabled={exporting || !cloneDoc}><Icon.download size={14} /><span className="btn-label">Export</span></button>}
      {!phone && importButton}
    </div>
  )
}
