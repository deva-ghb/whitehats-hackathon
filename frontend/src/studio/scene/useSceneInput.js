// What the person does to the scene: press, drag, release, wheel, keys and drops from the library. It turns each into a
// call on the editor (`events`) in the clone's ground coordinates, and keeps the state the drawing shows meanwhile
// (what is hovered, being dragged, resized, connected or boxed in).
import { useCallback, useEffect, useRef, useState } from 'react'
import { GROUP_MIN, descendants, topOnes } from '../geometry'
import { pickAt, pickEdge, pickWithin } from './hit'
import { groundToScene, screenToGround, screenToScene, sceneToScreen, zoomAbout } from './project'

const DRAG_START = 4          // pixels before a press becomes a drag
const HANDLE_REACH = 13       // screen pixels around a handle that count as hitting it
const PAN_STEP = 60

export function useSceneInput({ view, sceneRef, byIdRef, docRef, idsRef, editing, events, dragRef, tipRef }) {
  const { svg, root, viewRef, sizeRef, touched, applyView, local, pointAt, zoomBy, panBy, fit, settle } = view
  const session = useRef(null)
  const pointers = useRef(new Map())
  const handles = useRef([])
  const [hover, setHover] = useState(null)
  const [focusId, setFocusId] = useState(null)
  const [moved, setMoved] = useState(null)
  const [ghost, setGhost] = useState(null)
  const [connecting, setConnecting] = useState(null)
  const [resizing, setResizing] = useState(null)
  const [marquee, setMarquee] = useState(null)
  const [panning, setPanning] = useState(false)
  const [overItem, setOverItem] = useState(false)
  const [dropping, setDropping] = useState(false)
  const connectingRef = useRef(null)
  connectingRef.current = connecting

  // The handles of the selected thing: a dot to draw a connection from, and for a zone its four corners.
  const handlesFor = useCallback(() => {
    const out = []
    if (!editing || idsRef.current.length !== 1) return out
    const id = idsRef.current[0]
    const thing = byIdRef.current.get(id)
    if (!thing) return out
    out.push({ kind: 'connect', id, x: thing.bounds.x + thing.bounds.width, y: thing.bounds.y + thing.bounds.height / 2, dx: 16, dy: 0 })
    if (thing.isZone) {
      const box = sceneRef.current.boxes.get(id)
      const corners = { nw: [box.x, box.y], ne: [box.x + box.width, box.y], se: [box.x + box.width, box.y + box.height], sw: [box.x, box.y + box.height] }
      for (const [corner, [gx, gy]] of Object.entries(corners)) {
        const [x, y] = groundToScene(gx, gy)
        out.push({ kind: 'resize', id, corner, x, y, dx: 0, dy: 0 })
      }
    }
    return out
  }, [byIdRef, editing, idsRef, sceneRef])

  // What is under a pointer on the pane: a handle, a person or solid, a connection, a zone, or nothing.
  const grabAt = useCallback((px, py) => {
    const current = viewRef.current
    const [sx, sy] = screenToScene(current, px, py)
    for (const handle of handles.current) {
      const [hx, hy] = sceneToScreen(current, handle.x, handle.y)
      if (Math.hypot(hx + handle.dx - px, hy + handle.dy - py) <= HANDLE_REACH) return { kind: handle.kind, handle }
    }
    const scene = sceneRef.current
    const solid = pickAt(scene.floats, sx, sy) || pickAt(scene.solids, sx, sy)
    if (solid) return { kind: 'item', id: solid }
    const edge = pickEdge(scene.edges, sx, sy, 6 / current.zoom)
    if (edge) return { kind: 'edge', edge: scene.edges.find((entry) => entry.id === edge) }
    const zone = pickAt([...scene.zones].reverse(), sx, sy)
    if (zone) return { kind: 'item', id: zone, zone: true }
    return { kind: 'blank' }
  }, [sceneRef, viewRef])

  const hoverTo = useCallback((grab, px, py) => {
    const next = grab.kind === 'item' ? `i:${grab.id}` : grab.kind === 'edge' ? `e:${grab.edge.id}` : null
    setOverItem(grab.kind !== 'blank')
    setHover((previous) => {
      if ((previous?.key || null) === next) return previous
      return next ? { key: next, id: grab.id, edge: grab.edge, x: px, y: py } : null
    })
    if (tipRef.current) {
      tipRef.current.style.left = `${Math.min(px + 16, sizeRef.current.width - 296)}px`
      tipRef.current.style.top = `${py + 18}px`
    }
  }, [sizeRef, tipRef])

  const movingSet = (ids) => {
    const roots = topOnes(docRef.current, ids)
    const all = new Set(roots)
    for (const id of roots) for (const kid of descendants(docRef.current, id)) all.add(kid)
    return { roots, all }
  }
  const groundOf = (px, py) => screenToGround(viewRef.current, px, py)
  const sceneAt = (px, py) => screenToScene(viewRef.current, px, py)

  const onPointerDown = (event) => {
    if (event.button > 1 || !svg.current) return
    svg.current.setPointerCapture(event.pointerId)
    const [px, py] = local(event.clientX, event.clientY)
    pointers.current.set(event.pointerId, [px, py])
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      session.current = { kind: 'pinch', distance: Math.hypot(a[0] - b[0], a[1] - b[1]), view: { ...viewRef.current }, mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] }
      return
    }
    if (event.button === 1 || event.getModifierState?.(' ')) {
      session.current = { kind: 'pan', start: [px, py], view: { ...viewRef.current } }
      touched.current = true
      setPanning(true)
      event.preventDefault()
      return
    }
    handles.current = handlesFor()
    const grab = grabAt(px, py)
    const kind = grab.kind === 'connect' || grab.kind === 'resize' ? grab.kind : 'press'
    session.current = { kind, grab, start: [px, py], ground: groundOf(px, py), modifier: event.shiftKey || event.ctrlKey || event.metaKey, shift: event.shiftKey }
    if (kind === 'connect') setConnecting({ from: grab.handle.id, at: sceneAt(px, py), target: null })
  }

  // A press that moved far enough becomes a drag of what was under it, a box, or a pan of the view.
  const beginDrag = (s, px, py) => {
    const { grab } = s
    if (grab.kind === 'item' && editing) {
      const base = idsRef.current
      let ids = base
      if (!base.includes(grab.id)) {
        ids = s.modifier ? [...base, grab.id] : [grab.id]
        events.onSelect(ids)
      }
      const { roots, all } = movingSet(ids)
      Object.assign(s, { kind: 'move', roots, all })
      return
    }
    if (grab.kind === 'blank' && s.shift) {
      s.kind = 'marquee'
      return
    }
    Object.assign(s, { kind: 'pan', view: { ...viewRef.current }, start: [px, py] })
    touched.current = true
    setPanning(true)
  }

  const resizeBox = (s, px, py) => {
    const now = groundOf(px, py)
    const box = sceneRef.current.boxes.get(s.grab.handle.id)
    const { corner } = s.grab.handle
    const left = corner.includes('w') ? Math.min(now.x, box.x + box.width - GROUP_MIN.width) : box.x
    const right = corner.includes('e') ? Math.max(now.x, box.x + GROUP_MIN.width) : box.x + box.width
    const top = corner.includes('n') ? Math.min(now.y, box.y + box.height - GROUP_MIN.height) : box.y
    const bottom = corner.includes('s') ? Math.max(now.y, box.y + GROUP_MIN.height) : box.y + box.height
    return { x: left, y: top, width: right - left, height: bottom - top }
  }

  const targetAt = (px, py) => {
    const [sx, sy] = sceneAt(px, py)
    const scene = sceneRef.current
    return pickAt(scene.floats, sx, sy) || pickAt(scene.solids, sx, sy) || pickAt([...scene.zones].reverse(), sx, sy)
  }

  const onPointerMove = (event) => {
    const [px, py] = local(event.clientX, event.clientY)
    if (pointers.current.has(event.pointerId)) pointers.current.set(event.pointerId, [px, py])
    const s = session.current
    if (!s) {
      if (event.pointerType === 'mouse' || event.pointerType === 'pen') {
        handles.current = handlesFor()
        hoverTo(grabAt(px, py), px, py)
      }
      return
    }
    if (s.kind === 'pinch') {
      if (pointers.current.size < 2) return
      const [a, b] = [...pointers.current.values()]
      const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
      const zoomed = zoomAbout(s.view, s.mid[0], s.mid[1], s.view.zoom * (Math.hypot(a[0] - b[0], a[1] - b[1]) / s.distance))
      applyView({ ...zoomed, x: zoomed.x + (mid[0] - s.mid[0]), y: zoomed.y + (mid[1] - s.mid[1]) })
    } else if (s.kind === 'pan') {
      applyView({ zoom: s.view.zoom, x: s.view.x + px - s.start[0], y: s.view.y + py - s.start[1] })
    } else if (s.kind === 'connect') {
      const target = targetAt(px, py)
      setConnecting({ from: s.grab.handle.id, at: sceneAt(px, py), target: target && target !== s.grab.handle.id ? target : null })
    } else if (s.kind === 'resize') {
      s.box = resizeBox(s, px, py)
      setResizing({ id: s.grab.handle.id, box: s.box })
    } else if (s.kind === 'marquee') {
      s.rect = { x: Math.min(s.start[0], px), y: Math.min(s.start[1], py), width: Math.abs(px - s.start[0]), height: Math.abs(py - s.start[1]) }
      setMarquee(s.rect)
    } else if (s.kind === 'press') {
      if (Math.hypot(px - s.start[0], py - s.start[1]) < DRAG_START) return
      beginDrag(s, px, py)
      onPointerMove(event)
    } else if (s.kind === 'move') {
      const now = groundOf(px, py)
      s.delta = [now.x - s.ground.x, now.y - s.ground.y]
      setMoved(new Map([...s.all].map((id) => [id, s.delta])))
    }
  }

  // A press that did not become a drag is a click: select, add to the selection, pick a connection, or clear.
  const click = (s, event) => {
    const { grab } = s
    if (grab.kind === 'item') {
      const base = idsRef.current
      if (s.modifier) events.onSelect(base.includes(grab.id) ? base.filter((id) => id !== grab.id) : [...base, grab.id])
      else events.onSelect([grab.id])
      svg.current?.querySelector(`[data-scene-item][data-id="${CSS.escape(grab.id)}"]`)?.focus({ preventScroll: true })
    } else if (grab.kind === 'edge') {
      events.onSelectEdge(grab.edge.edge)
    } else {
      if (!s.modifier) events.onBlank()
      if (event.pointerType === 'touch') setHover(null)
    }
  }

  const endMarquee = (s) => {
    setMarquee(null)
    const [x0, y0] = sceneAt(s.rect.x, s.rect.y)
    const [x1, y1] = sceneAt(s.rect.x + s.rect.width, s.rect.y + s.rect.height)
    const area = { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
    const inside = ({ bounds }) => bounds.x >= area.x && bounds.y >= area.y && bounds.x + bounds.width <= area.x + area.width && bounds.y + bounds.height <= area.y + area.height
    const scene = sceneRef.current
    const ids = [...pickWithin([...scene.solids, ...scene.floats], area), ...scene.zones.filter(inside).map((zone) => zone.id)]
    if (ids.length) events.onSelect(ids)
  }

  const finish = (event, cancelled = false) => {
    const s = session.current
    pointers.current.delete(event.pointerId)
    if (svg.current?.hasPointerCapture?.(event.pointerId)) svg.current.releasePointerCapture(event.pointerId)
    if (!s) return
    if (s.kind === 'pinch') {
      if (pointers.current.size < 2) session.current = null
      settle()
      return
    }
    session.current = null
    setPanning(false)
    if (cancelled) { setMoved(null); setConnecting(null); setResizing(null); setMarquee(null); return }
    if (s.kind === 'press') click(s, event)
    else if (s.kind === 'move') {
      const was = new Map()
      for (const id of s.roots) {
        const box = s.delta && sceneRef.current.boxes.get(id)
        if (box) was.set(id, { x: box.x + s.delta[0], y: box.y + s.delta[1] })
      }
      setMoved(null)
      if (was.size) events.onMoveEnd(was)
    } else if (s.kind === 'resize') {
      setResizing(null)
      if (s.box) events.onResizeEnd(s.grab.handle.id, s.box)
    } else if (s.kind === 'connect') {
      const target = connectingRef.current?.target
      setConnecting(null)
      if (target) events.onConnect(s.grab.handle.id, target)
    } else if (s.kind === 'marquee') endMarquee(s)
  }

  // The wheel zooms about the pointer.
  const onWheel = useCallback((event) => {
    event.preventDefault()
    touched.current = true
    const [px, py] = local(event.clientX, event.clientY)
    const current = viewRef.current
    applyView(zoomAbout(current, px, py, current.zoom * Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0016))))
  }, [applyView, local, touched, viewRef])
  useEffect(() => {
    const element = svg.current
    element?.addEventListener('wheel', onWheel, { passive: false })
    return () => element?.removeEventListener('wheel', onWheel)
  }, [onWheel, svg])

  // Enter or Space on a focused solid, zone or connection selects it; on the bare pane the keys zoom and pan.
  const onKeyDown = (event) => {
    const { target } = event
    if (target.closest?.('[data-scene-item], [data-scene-edge]')) {
      if ((event.key === 'Enter' || event.key === ' ') && !event.shiftKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault()
        const item = target.closest('[data-scene-item]')
        if (item) events.onSelect([item.getAttribute('data-id')])
        else {
          const key = target.closest('[data-scene-edge]').getAttribute('data-scene-edge')
          const edge = sceneRef.current.edges.find((entry) => entry.id === key)
          if (edge) events.onSelectEdge(edge.edge)
        }
      }
      return
    }
    if (target !== svg.current && target !== root.current) return
    const step = event.shiftKey ? PAN_STEP * 3 : PAN_STEP
    if (event.key === '+' || event.key === '=') zoomBy(1.25)
    else if (event.key === '-') zoomBy(0.8)
    else if (event.key === '0') fit()
    else if (event.key === 'ArrowLeft') panBy(step, 0)
    else if (event.key === 'ArrowRight') panBy(-step, 0)
    else if (event.key === 'ArrowUp') panBy(0, step)
    else if (event.key === 'ArrowDown') panBy(0, -step)
    else return
    event.preventDefault()
  }
  const onFocus = (event) => {
    const proxy = event.target.closest?.('[data-scene-item]')
    setFocusId(proxy && event.target.matches(':focus-visible') ? proxy.getAttribute('data-id') : null)
  }

  // A type dragged from the library: a ghost of its footprint follows the pointer, and dropping places it under the pointer.
  const onDragOver = (event) => {
    if (!editing || !dragRef.current) return
    event.preventDefault()
    setDropping(true)
    setGhost({ at: pointAt(event.clientX, event.clientY), group: dragRef.current.family === 'boundaries' })
  }
  const onDrop = (event) => {
    if (!editing) return
    event.preventDefault()
    const type = dragRef.current
    dragRef.current = null
    setDropping(false)
    setGhost(null)
    if (type) events.onDropType(type, pointAt(event.clientX, event.clientY))
  }

  return {
    state: { hover, focusId, moved, ghost, connecting, resizing, marquee, panning, overItem, dropping },
    handles, session,
    svgProps: {
      onPointerDown, onPointerMove, onPointerUp: finish, onPointerCancel: (event) => finish(event, true),
      onPointerLeave: () => { if (!session.current) { setHover(null); setOverItem(false) } },
      onKeyDown, onFocus, onBlur: () => setFocusId(null),
    },
    dropProps: { onDragOver, onDrop, onDragLeave: () => { setDropping(false); setGhost(null) } },
    refreshHandles: () => { handles.current = handlesFor() },
  }
}
