// The editing controller: everything a person can do to a clone, counted in the clone's own coordinates.
//
// THE BOUNDARY (STUDIO.1 / STUDIO.2). This file, edit.js, geometry.js and session.js know nothing about how the clone is
// drawn. A "surface" (FlatCanvas today, the isometric scene later) draws it and tells this controller what the person did:
//   surface -> controller   onSelect(ids), onSelectEdge(edge), onBlank(), onMoveEnd(Map id -> {x, y}), onResizeEnd(id, box),
//                           onConnect(fromId, toId), onDropType(type, point), onBoxes(Map id -> box)
//   controller -> surface   a ref with pointAt(clientX, clientY) -> point, viewCentre() -> point, fit(), reveal(id)
// Every point and box is in ground units: x and y across the ground, width and depth for a zone, absolute (not counted from a
// parent). A flat canvas reads them off the screen; an isometric scene projects the pointer onto the ground plane first.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useToast } from '../components/ui'
import {
  addEdge, groupItems, placeType, removeEdge, removeItems, setEdgeType, setField, setName, setProperty,
} from './edit'
import {
  absoluteBoxes, alignItems, clearLayout, hasStoredLayout, descendants, distributeItems, freezeLayout, moveItems, nudgeItems, reparentItem, resizeGroup,
} from './geometry'
import { isConnectionType } from './connection'
import { accessibleName, describe, humanKey, problems as findProblems } from './model'

export function useEditor({ edits, document: cloneDoc, library, types, surface, boxes, editing }) {
  const toast = useToast()
  const { apply, undo, redo, lastStep, draftRef } = edits
  const [sel, setSel] = useState(null)
  const [pending, setPending] = useState(null)
  const [grouping, setGrouping] = useState(false)
  const [snapOn, setSnapOn] = useState(true)

  const items = useMemo(() => new Map([...(cloneDoc?.nodes || []), ...(cloneDoc?.groups || [])].map((item) => [item.id, item])), [cloneDoc])
  const names = useMemo(() => new Map([...(cloneDoc?.nodes || []), ...(cloneDoc?.groups || []), ...(cloneDoc?.access_paths || []),
    ...(cloneDoc?.credential_refs || []).map((c) => ({ id: c.id, name: c.purpose }))].map((item) => [item.id, item.name])), [cloneDoc])
  const issues = useMemo(() => (cloneDoc && library.data ? findProblems(cloneDoc, library.data.types) : []), [cloneDoc, library.data])
  const ids = useMemo(() => (sel?.kind === 'item' ? sel.ids.filter((id) => items.has(id)) : []), [sel, items])

  // Geometry edits start from what is on screen: the first one stores the places the auto layout chose.
  const edit = useCallback((mutator, reason, options) => apply((doc) => {
    const placed = freezeLayout(doc, boxes.current)
    const out = mutator(placed)
    return out === placed ? doc : out
  }, reason, options), [apply, boxes])

  const nameOf = (id) => names.get(id) || 'an item'
  const label = (list) => (list.length === 1 ? nameOf(list[0]) : `${list.length} items`)

  const place = useCallback((type, at) => {
    if (type.generic) {
      toast('A Generic type is a placeholder, so it cannot go into a clone. Choose a proper type from the library.', 'info')
      return
    }
    // One node stands for a connection: a second would have nothing of its own to find, so the one there is selected.
    const there = isConnectionType(type.id) && cloneDoc?.groups.find((g) => g.type.split('@')[0] === type.id)
    if (there) {
      toast(`${type.display_name} is already on the canvas. Its setup is in the inspector.`, 'info')
      setSel({ kind: 'item', ids: [there.id] })
      surface.current.reveal(there.id)
      return
    }
    const group = ids.length === 1 && cloneDoc?.groups.some((g) => g.id === ids[0]) ? ids[0] : null
    let made = null
    const ok = edit((doc) => {
      const result = placeType(doc, type, at ? { at, avoid: false } : group ? { parentId: group } : { at: surface.current.viewCentre() })
      made = result.id
      return result.document
    }, `Placed ${type.display_name}`)
    if (ok && made) {
      setSel({ kind: 'item', ids: [made] })
      surface.current.reveal(made)
    }
  }, [cloneDoc, edit, ids, surface, toast])

  const moveEnd = useCallback((moves) => {
    if (!moves.size) return
    edit((doc) => moveItems(doc, moves, { reparent: true, onGrid: snapOn }), `Moved ${label([...moves.keys()])}`)
  }, [edit, snapOn, names])

  const resizeEnd = useCallback((id, box) => {
    const snap = (value) => (snapOn ? Math.round(value / 8) * 8 : Math.round(value))
    edit((doc) => resizeGroup(doc, id, { x: box.x, y: box.y, width: snap(box.width), height: snap(box.height) }), `Resized ${nameOf(id)}`)
  }, [edit, snapOn, names])

  const nudge = useCallback((dx, dy) => {
    edit((doc) => nudgeItems(doc, ids, dx, dy), `Moved ${label(ids)}`, { wait: 500, key: `nudge:${ids.join()}` })
  }, [edit, ids, names])

  const resizeBy = useCallback((dw, dh) => {
    const id = ids[0]
    edit((doc) => {
      const box = absoluteBoxes(doc).get(id)
      return box && doc.groups.some((g) => g.id === id) ? resizeGroup(doc, id, { ...box, width: box.width + dw, height: box.height + dh }) : doc
    }, `Resized ${nameOf(id)}`, { wait: 500, key: `resize:${id}` })
  }, [edit, ids, names])

  const remove = useCallback(() => {
    const doc = draftRef.current
    if (!editing || !doc || !sel) return
    const words = sel.kind === 'edge' ? humanKey(sel.type) : label(ids)
    const done = sel.kind === 'edge'
      ? apply((d) => removeEdge(d, sel.from, sel.to, sel.type), `Removed ${words}`)
      : ids.length > 0 && apply((d) => removeItems(d, ids), `Removed ${words}`)
    if (!done) return
    const mark = lastStep()
    setSel(null)
    toast(`Removed ${words}.`, 'info', {
      label: 'Undo', ms: 8000,
      onClick: () => (lastStep() === mark ? undo() : toast('More changes were made since. Use Undo on the toolbar to step back.', 'info')),
    })
  }, [apply, draftRef, editing, ids, lastStep, names, sel, toast, undo])

  const connectEnd = useCallback((from, to) => {
    if (!editing || !from || !to || from === to) return
    setPending({ fromId: from, toId: to })
  }, [editing])
  const addConnection = useCallback((to, edgeType) => {
    if (!pending) return
    let added = false
    apply((doc) => {
      const result = addEdge(doc, pending.fromId, to, edgeType, types)
      if (result.refusal) return doc
      added = true
      return result.document
    }, `Connected ${nameOf(pending.fromId)} to ${nameOf(to)} (${humanKey(edgeType)})`)
    if (added) {
      setSel({ kind: 'edge', from: pending.fromId, to, type: edgeType })
      setPending(null)
    }
  }, [apply, names, pending, types])

  const group = useCallback((type) => {
    let refusal = null
    let made = null
    const ok = edit((doc) => {
      const result = groupItems(doc, ids, type)
      if (result.refusal) { refusal = result.refusal; return doc }
      made = result.id
      return result.document
    }, `Grouped ${ids.length} items in ${type.display_name}`)
    if (refusal) toast(refusal, 'error')
    if (ok && made) { setSel({ kind: 'item', ids: [made] }); setGrouping(false) }
  }, [edit, ids, toast])

  const changeEdgeType = useCallback((next) => {
    if (sel?.kind !== 'edge') return null
    let refusal = null
    apply((doc) => {
      const result = setEdgeType(doc, sel, next, types)
      if (result.refusal) { refusal = result.refusal; return doc }
      return result.document
    }, `Changed ${nameOf(sel.from)} to ${nameOf(sel.to)} to ${humanKey(next)}`)
    if (!refusal) setSel({ ...sel, type: next })
    return refusal
  }, [apply, names, sel, types])

  const reparent = useCallback((id, parentId) => {
    edit((doc) => {
      const result = reparentItem(doc, id, parentId)
      if (result.refusal) { toast(result.refusal, 'error'); return doc }
      return result.document
    }, `Moved ${nameOf(id)} ${parentId ? `into ${nameOf(parentId)}` : 'out of its container'}`)
  }, [edit, names, toast])

  // Inspector actions. Typing in a field is one step in the history and one version, however long it takes.
  const inspector = useMemo(() => ({
    name: (id, name) => apply((doc) => setName(doc, id, name), `Renamed ${name}`, { wait: 400, key: `name:${id}` }),
    field: (id, key, value) => apply((doc) => setField(doc, id, key, value), `Set ${humanKey(key)} on ${nameOf(id)}`, { wait: 400, key: `field:${key}:${id}` }),
    property: (id, key, value) => apply((doc) => setProperty(doc, id, key, value), `Set ${humanKey(key)} on ${nameOf(id)}`, { wait: 400, key: `prop:${key}:${id}` }),
    parent: reparent,
    connect: () => ids.length === 1 && setPending({ fromId: ids[0] }),
    remove,
    place,
    edgeType: changeEdgeType,
    align: (mode) => edit((doc) => alignItems(doc, ids, mode), `Lined up ${ids.length} items (${mode})`),
    distribute: (axis) => edit((doc) => distributeItems(doc, ids, axis), `Spread ${ids.length} items ${axis === 'horizontal' ? 'across' : 'down'}`),
    group: () => setGrouping(true),
  }), [apply, changeEdgeType, edit, ids, names, place, remove, reparent])

  // What the Inspector shows for the selection.
  const selection = useMemo(() => {
    if (!sel || !cloneDoc) return null
    if (sel.kind === 'type') return sel
    if (sel.kind === 'item') {
      if (!ids.length) return null
      const parts = ids.map((id) => ({ item: items.get(id), type: types.get(items.get(id).type) }))
      if (parts.length > 1) return { kind: 'items', items: parts }
      const [{ item, type }] = parts
      const blocked = new Set([item.id, ...descendants(cloneDoc, item.id)])
      const groups = [{ value: 'top', label: 'Top level (in no container)' },
        ...cloneDoc.groups.filter((g) => !blocked.has(g.id)).map((g) => ({ value: g.id, label: g.name, description: types.get(g.type)?.display_name }))]
      return { kind: 'item', item, type, groups }
    }
    const edge = (cloneDoc.edges || []).find((entry) => entry.from === sel.from && entry.to === sel.to && entry.type === sel.type)
    if (!edge) return null
    const taken = cloneDoc.edges.filter((e) => e.from === edge.from && e.to === edge.to && e.type !== edge.type).map((e) => e.type)
    return { kind: 'edge', edge, fromType: types.get(items.get(edge.from)?.type), toType: types.get(items.get(edge.to)?.type), taken }
  }, [sel, cloneDoc, items, ids, types])

  const relayoutEdit = useCallback(() => {
    apply((doc) => (hasStoredLayout(doc) ? clearLayout(doc) : doc), 'Laid the clone out again')
    surface.current.relayout()
  }, [apply, surface])

  const selectAll = useCallback(() => setSel({ kind: 'item', ids: [...items.keys()] }), [items])
  const toggle = useCallback((id) => setSel((prev) => {
    const now = new Set(prev?.kind === 'item' ? prev.ids : [])
    if (now.has(id)) now.delete(id)
    else now.add(id)
    return now.size ? { kind: 'item', ids: [...now] } : null
  }), [])

  // Handed to the keyboard hook through a ref, so the key listener is not rebuilt on every render.
  const keys = useRef(null)
  keys.current = { undo, redo, selectAll, group: () => ids.length && setGrouping(true), resize: resizeBy, nudge, toggle, remove,
    connect: inspector.connect, settings: () => window.document.getElementById('item-name')?.focus() }
  const keyActions = useMemo(() => Object.fromEntries(['undo', 'redo', 'selectAll', 'group', 'resize', 'nudge', 'toggle', 'remove', 'connect', 'settings']
    .map((name) => [name, (...args) => keys.current[name](...args)])), [])

  // What the surface may ask for each item: its words for a screen reader, its state, and (while a connection is drawn) its fit.
  const describeItem = useCallback((id, fallback) => {
    const item = items.get(id) || fallback
    if (!item) return { item: undefined, type: undefined, described: { state: 'unknown', missing: [] }, ariaLabel: '' }
    const type = types.get(item.type)
    const described = describe(item, type)
    return { item, type, described, ariaLabel: accessibleName(item, type, described) }
  }, [items, types])

  useEffect(() => { setSel(null); setPending(null); setGrouping(false) }, [cloneDoc?.clone])

  const surfaceEvents = useMemo(() => ({
    onSelect: (picked) => setSel(picked.length ? { kind: 'item', ids: picked } : null),
    onSelectEdge: (edge) => setSel({ kind: 'edge', from: edge.from, to: edge.to, type: edge.type }),
    onBlank: () => setSel(null),
    onMoveEnd: moveEnd,
    onResizeEnd: resizeEnd,
    onConnect: connectEnd,
    onDropType: place,
    onBoxes: (next) => { boxes.current = next },
  }), [boxes, connectEnd, moveEnd, place, resizeEnd])

  return {
    sel, setSel, ids, items, names, issues, selection, pending, setPending, grouping, setGrouping, snapOn, setSnapOn,
    inspector, keyActions, surfaceEvents, addConnection, group, place, describeItem, relayoutEdit,
  }
}
