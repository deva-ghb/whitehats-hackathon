import { useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from '../components/ui'

// KS.1: the knowledge sphere as a globe. Concepts are spread over it, their rules and exceptions clustered around them,
// relationships drawn as arcs over the surface. Drag to spin (with momentum), scroll or the buttons to zoom, choose an
// item to bring it to the front. It flows into place on load, then turns very slowly; with "reduce motion" set it is
// still. Plain maths on HTML and SVG: text stays sharp, no 3D library.

const CLUSTER = 0.78 // how far (in radians) a concept's rules sit from it
const APART = 0.44 // the least angle between any two items
const ENTER_MS = 1100
const FRICTION = 0.92
const TURN_MS = 650 // how long a chosen item takes to turn to the front
const FRONT = 0.12 // nearer than this can be chosen; further, the card is behind the globe

const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s]
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const norm = (a) => { const l = Math.hypot(...a) || 1; return scale(a, 1 / l) }
const apply = (m, v) => [dot(m[0], v), dot(m[1], v), dot(m[2], v)]
const mul = (a, b) => a.map((row) => [0, 1, 2].map((j) => row[0] * b[0][j] + row[1] * b[1][j] + row[2] * b[2][j]))
const IDENTITY = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

/** The rotation of `angle` radians about the unit `axis`. */
function rotation(axis, angle) {
  const [x, y, z] = axis
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const t = 1 - c
  return [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
    [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
    [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
}

/** `count` points spread evenly over the sphere (a Fibonacci lattice). */
function spread(count) {
  if (count === 1) return [[0, 0, 1]]
  const golden = Math.PI * (3 - Math.sqrt(5))
  return Array.from({ length: count }, (_, i) => {
    const y = 1 - (i / (count - 1)) * 2
    const r = Math.sqrt(1 - y * y)
    return [Math.cos(golden * i) * r, y, Math.sin(golden * i) * r]
  })
}

/** Where each node sits on the unit sphere, and where it flows out from on load. */
function place(nodes, edges) {
  const home = {}
  for (const e of edges) if (e.kind === 'ABOUT' && !home[e.source]) home[e.source] = e.target
  for (const e of edges) if (e.kind === 'EXEMPTS' && !home[e.source] && home[e.target]) home[e.source] = home[e.target]
  const concepts = nodes.filter((n) => n.kind === 'Concept')
  const members = Object.fromEntries(concepts.map((c) => [c.key, []]))
  const loose = []
  for (const n of nodes) {
    if (n.kind === 'Concept') continue
    if (home[n.key] && members[home[n.key]]) members[home[n.key]].push(n)
    else loose.push(n)
  }
  // The busiest concepts first, so the lattice gives them the most room.
  concepts.sort((a, b) => members[b.key].length - members[a.key].length)
  const anchors = spread(concepts.length + loose.length)
  const dir = {}
  const from = {}
  concepts.forEach((c, i) => {
    const centre = anchors[i]
    dir[c.key] = centre
    from[c.key] = [0, 0, 1]
    const list = members[c.key]
    // A tangent frame at the concept, to lay its members in rings around it.
    const up = Math.abs(centre[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0]
    const u = norm(cross(up, centre))
    const v = cross(centre, u)
    list.forEach((n, j) => {
      const ring = j < 6 ? 0 : 1
      const inRing = ring ? list.length - 6 : Math.min(6, list.length)
      const k = ring ? j - 6 : j
      const a = (k / inRing) * Math.PI * 2 + ring * 0.5
      const off = CLUSTER * (1 + ring * 0.75)
      dir[n.key] = norm(add(scale(centre, Math.cos(off)), add(scale(u, Math.sin(off) * Math.cos(a)), scale(v, Math.sin(off) * Math.sin(a)))))
      from[n.key] = centre
    })
  })
  loose.forEach((n, i) => { dir[n.key] = anchors[concepts.length + i]; from[n.key] = [0, 0, 1] })
  // A few rounds of repulsion on the surface, so no two items sit on top of each other.
  const keys = Object.keys(dir)
  for (let round = 0; round < 80; round += 1) {
    for (let i = 0; i < keys.length; i += 1) {
      for (let j = i + 1; j < keys.length; j += 1) {
        const a = dir[keys[i]]
        const b = dir[keys[j]]
        const d = Math.acos(Math.max(-1, Math.min(1, dot(a, b))))
        if (d < APART) {
          const push = scale(norm(add(a, scale(b, -1))), (APART - d) * 0.5)
          dir[keys[i]] = norm(add(a, push))
          dir[keys[j]] = norm(add(b, scale(push, -1)))
        }
      }
    }
  }
  return { dir, from }
}

/** The globe's mesh: points spread evenly over it, each joined to its nearest neighbours, so the surface reads as a
 * web of triangles. A few points are hubs that glow. It only gives the globe its shape: real links are drawn over it. */
const MESH = (() => {
  const points = spread(150)
  const seen = new Set()
  const links = []
  points.forEach((a, i) => {
    points.map((b, j) => [j, dot(a, b)]).filter(([j]) => j !== i).sort((x, y) => y[1] - x[1]).slice(0, 5)
      .forEach(([j]) => {
        const id = i < j ? `${i}-${j}` : `${j}-${i}`
        if (!seen.has(id)) { seen.add(id); links.push([i, j]) }
      })
  })
  const hubs = points.map((_, i) => i).filter((i) => (i * 37) % 11 === 0) // a fixed, even scatter of about one in eleven
  return { points, links, hubs }
})()

/** The arc between two points of the sphere, lifted a little above its surface. */
function arc(a, b, steps = 28) {
  const d = Math.acos(Math.max(-1, Math.min(1, dot(a, b))))
  if (d < 1e-4) return [a, b]
  if (d > Math.PI - 0.2) {
    // Opposite sides have no one shortest way round: go by a point a quarter turn from both.
    const side = norm(cross(a, Math.abs(a[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0]))
    const mid = norm(add(side, scale(add(a, b), 0.5)))
    return [...arc(a, mid, steps / 2), ...arc(mid, b, steps / 2).slice(1)]
  }
  return Array.from({ length: steps + 1 }, (_, i) => {
    const t = i / steps
    const s = Math.sin(d)
    const p = add(scale(a, Math.sin((1 - t) * d) / s), scale(b, Math.sin(t * d) / s))
    return scale(p, 1 + Math.sin(t * Math.PI) * 0.06 * Math.min(1, d))
  })
}

const easeOut = (t) => 1 - (1 - t) ** 3
const easeInOut = (t) => (t < 0.5 ? 4 * t ** 3 : 1 - (-2 * t + 2) ** 3 / 2)
// Room around the globe for the cards on its rim; less on a narrow screen.
const radius = (size) => Math.max(110, Math.min(size.w / 2 - (size.w < 600 ? 28 : 70), size.h / 2 - 60))

export default function SphereGlobe({ nodes, edges, diff, selected, onPick, stateOf, label }) {
  const wrap = useRef(null)
  const [size, setSize] = useState({ w: 900, h: 620 })
  // The page polls the server; each answer is a new list even when nothing changed. The layout follows what the
  // sphere holds (its items and links), so an unchanged answer moves nothing.
  const shape = useMemo(() => JSON.stringify([nodes.map((n) => [n.key, n.kind]).sort(),
    edges.map((e) => `${e.kind}|${e.source}|${e.target}`).sort()]), [nodes, edges])
  const { dir, from } = useMemo(() => place(nodes, edges), [shape]) // eslint-disable-line react-hooks/exhaustive-deps
  // When each item first appeared: only new items flow into place, the rest stay where they are.
  const born = useRef(new Map())
  const els = useRef(new Map())
  const edgeEls = useRef(new Map())
  const grat = useRef({ front: null, back: null, hubs: [] })
  const view = useRef({ m: mul(rotation([1, 0, 0], -0.32), IDENTITY), zoom: 1, vx: 0, vy: 0, enter: performance.now(),
    touched: 0, drag: null, moved: 0, focus: null, hover: false })
  const still = useRef(typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
  const near = useMemo(() => (selected ? new Set([selected, ...edges.filter((e) => e.source === selected || e.target === selected)
    .flatMap((e) => [e.source, e.target])]) : null), [selected, edges])

  useEffect(() => {
    if (!wrap.current) return undefined
    const seen = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }))
    seen.observe(wrap.current)
    return () => seen.disconnect()
  }, [])

  // A new set of items flows into place again.
  useMemo(() => { // recorded before the first frame is drawn, so nothing is shown in place and then sent back
    const now = performance.now()
    for (const key of Object.keys(dir)) if (!born.current.has(key)) born.current.set(key, now)
    for (const key of [...born.current.keys()]) if (!dir[key]) born.current.delete(key)
  }, [dir])

  // Choosing an item turns the globe until it faces the viewer; clicking both focuses and selects it, one turn.
  const focusOn = (key) => { if (view.current.focus?.key !== key) view.current.focus = { key } }
  useEffect(() => {
    if (selected && dir[selected]) focusOn(selected)
  }, [selected, dir])

  useEffect(() => {
    let frame = 0
    const draw = (now) => {
      const st = view.current
      // Momentum from a drag and the turn towards a chosen item. The globe never turns on its own: it only moves when dragged.
      if (st.focus && dir[st.focus.key]) {
        // An eased turn of TURN_MS about one fixed axis: starts gently, ends gently, never a jump.
        if (!st.focus.axis) {
          const u = apply(st.m, dir[st.focus.key])
          const axis = cross(u, [0, 0, 1])
          st.focus.angle = Math.acos(Math.max(-1, Math.min(1, u[2])))
          st.focus.axis = Math.hypot(...axis) < 1e-6 ? null : norm(axis)
          st.focus.done = 0
          st.focus.start = now
          if (!st.focus.axis || st.focus.angle < 0.01) st.focus = null
        }
        if (st.focus) {
          const p = still.current ? 1 : Math.min(1, (now - st.focus.start) / TURN_MS)
          const goal = st.focus.angle * easeInOut(p)
          st.m = mul(rotation(st.focus.axis, goal - st.focus.done), st.m)
          st.focus.done = goal
          if (p >= 1) st.focus = null
        }
      } else if (!st.drag && (Math.abs(st.vx) > 1e-4 || Math.abs(st.vy) > 1e-4) && !still.current) {
        st.m = mul(rotation([0, 1, 0], st.vx), mul(rotation([1, 0, 0], st.vy), st.m))
        st.vx *= FRICTION
        st.vy *= FRICTION
      }
      const entering = (key) => (still.current ? 1 : easeOut(Math.min(1, (now - (born.current.get(key) ?? 0)) / ENTER_MS)))
      const at = (key) => { const e = entering(key); return e < 1 ? norm(add(scale(from[key], 1 - e), scale(dir[key], e))) : dir[key] }
      const enter = still.current ? 1 : easeOut(Math.min(1, (now - st.enter) / ENTER_MS)) // the globe itself, once
      const R = radius(size) * st.zoom * (0.82 + 0.18 * enter)
      const card = Math.min(1, Math.max(0.62, size.w / 760)) // cards shrink with a narrow screen, the globe less
      const clear = 0.45 // how near the front a card must be to read clearly (if no clearer card is in its way)
      const crowd = Math.max(0.62, Math.min(1, Math.sqrt(40 / Math.max(1, nodes.length)) * 1.15)) // and with many items
      const cx = size.w / 2
      const cy = size.h / 2
      const project = (v) => { const p = apply(st.m, v); return [cx + p[0] * R, cy - p[1] * R, p[2]] }
      const where = {}
      // Every item stays a card; the further round the globe, the smaller and fainter, and out of reach behind it.
      // Only the near cap reads clearly, nearest first: a card that would sit on a clearer one fades back with the rest,
      // so a full sphere is a globe, not a pile. The chosen item is always clear.
      const placed = []
      for (const [key, el] of els.current) {
        if (!dir[key]) continue
        const [x, y, z] = project(at(key))
        where[key] = [x, y, z]
        const s = (0.5 + 0.5 * ((z + 1) / 2) ** 1.5) * card * crowd
        placed.push({ key, el, x, y, z, s, w: (el.offsetWidth || 176) * s / 2 + 6, h: (el.offsetHeight || 60) * s / 2 + 4 })
      }
      placed.sort((a, b) => (b.key === selected) - (a.key === selected) || b.z - a.z)
      const sharp = []
      for (const p of placed) {
        const { key, el, x, y, z, s } = p
        const grown = entering(key)
        const front = z > FRONT
        if (el.dataset.side !== (front ? 'front' : 'back')) el.dataset.side = front ? 'front' : 'back'
        el.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%) scale(${s.toFixed(3)})`
        const crowded = sharp.some((q) => Math.abs(q.x - x) < q.w + p.w && Math.abs(q.y - y) < q.h + p.h)
        const isClear = key === selected || (z > clear && !crowded)
        if (isClear) sharp.push(p)
        const depth = Math.min(Math.max(0, z), clear * 0.98)
        const seen = isClear ? 0.8 + 0.2 * z : z > 0 ? 0.05 + 0.4 * (depth / clear) ** 3 : 0.02 + 0.04 * (z + 1)
        const blur = isClear || seen < 0.1 ? '' : `blur(${(1.6 * (1 - depth / clear)).toFixed(1)}px)`
        if (el.style.filter !== blur) el.style.filter = blur
        el.style.pointerEvents = isClear || (front && !crowded) ? '' : 'none'
        el.style.opacity = String(seen * (0.2 + 0.8 * grown))
        el.style.zIndex = String(Math.round((z + 1) * 500) + (isClear ? 1000 : 0))
      }
      for (const [id, el] of edgeEls.current) {
        const [, s, t] = id.split('|')
        if (!dir[s] || !dir[t]) continue
        const pts = arc(at(s), at(t)).map(project)
        el.setAttribute('d', pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(''))
        const depth = pts.reduce((sum, p) => sum + p[2], 0) / pts.length
        el.style.opacity = String(Math.max(0.03, Math.min(0.55, 0.05 + depth * 0.5)) * Math.min(entering(s), entering(t)))
      }
      // The mesh: the near half drawn, the far half faint; small dots at its points, larger ones nearer.
      const pts = MESH.points.map(project)
      let front = ''
      let back = ''
      for (const [i, j] of MESH.links) {
        const a = pts[i]
        const b = pts[j]
        const seg = `M${a[0].toFixed(1)},${a[1].toFixed(1)}L${b[0].toFixed(1)},${b[1].toFixed(1)}`
        if (a[2] + b[2] > 0) front += seg
        else back += seg
      }
      const ring = (p, r) => `M${(p[0] - r).toFixed(1)},${p[1].toFixed(1)}a${r},${r} 0 1 0 ${2 * r},0a${r},${r} 0 1 0 ${-2 * r},0`
      let dotsFront = ''
      let dotsBack = ''
      for (const p of pts) {
        if (p[2] > 0) dotsFront += ring(p, (1.4 + 1.8 * p[2]).toFixed(2))
        else dotsBack += ring(p, 1.2)
      }
      grat.current.dotsFront?.setAttribute('d', dotsFront)
      grat.current.dotsBack?.setAttribute('d', dotsBack)
      MESH.hubs.forEach((i, k) => {
        const el = grat.current.hubs?.[k]
        if (!el) return
        const p = pts[i]
        el.setAttribute('cx', p[0].toFixed(1))
        el.setAttribute('cy', p[1].toFixed(1))
        el.setAttribute('r', (Math.max(0, p[2]) * 9 + 3).toFixed(1))
        el.style.opacity = String(Math.max(0.08, 0.15 + 0.85 * p[2]))
      })
      grat.current.front?.setAttribute('d', front)
      grat.current.back?.setAttribute('d', back)
      grat.current.disc?.setAttribute('r', R.toFixed(1))
      grat.current.disc?.setAttribute('cx', cx)
      grat.current.disc?.setAttribute('cy', cy)
      frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [dir, from, size, selected, nodes.length])

  // Dragging spins the globe; a press that hardly moves is a click on whatever is under it.
  useEffect(() => {
    const el = wrap.current
    if (!el) return undefined
    const wheel = (e) => {
      e.preventDefault()
      const st = view.current
      st.zoom = Math.max(0.6, Math.min(2.4, st.zoom * Math.exp(-e.deltaY * 0.0015)))
      st.touched = performance.now()
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => el.removeEventListener('wheel', wheel)
  }, [])

  const down = (e) => {
    if (e.button !== 0) return
    const st = view.current
    st.drag = { x: e.clientX, y: e.clientY, id: e.pointerId }
    st.moved = 0
    st.focus = null
    st.touched = performance.now()
  }
  const move = (e) => {
    const st = view.current
    if (!st.drag || st.drag.id !== e.pointerId) return
    const dx = e.clientX - st.drag.x
    const dy = e.clientY - st.drag.y
    st.drag.x = e.clientX
    st.drag.y = e.clientY
    st.moved += Math.abs(dx) + Math.abs(dy)
    if (st.moved > 4 && !wrap.current.hasPointerCapture?.(e.pointerId)) wrap.current.setPointerCapture?.(e.pointerId)
    const R = radius(size) * st.zoom
    st.vx = dx / R
    st.vy = dy / R
    st.m = mul(rotation([0, 1, 0], st.vx), mul(rotation([1, 0, 0], st.vy), st.m))
    st.touched = performance.now()
  }
  const up = (e) => {
    const st = view.current
    if (!st.drag || st.drag.id !== e.pointerId) return
    st.drag = null
    st.touched = performance.now()
    if (wrap.current.hasPointerCapture?.(e.pointerId)) wrap.current.releasePointerCapture(e.pointerId)
  }
  const key = (e) => {
    const st = view.current
    const step = 0.12
    const turns = { ArrowLeft: [[0, 1, 0], -step], ArrowRight: [[0, 1, 0], step], ArrowUp: [[1, 0, 0], -step], ArrowDown: [[1, 0, 0], step] }
    if (turns[e.key]) {
      e.preventDefault()
      st.m = mul(rotation(...turns[e.key]), st.m)
      st.touched = performance.now()
    } else if (e.key === '+' || e.key === '=') st.zoom = Math.min(2.4, st.zoom * 1.15)
    else if (e.key === '-') st.zoom = Math.max(0.6, st.zoom / 1.15)
  }
  const zoomBy = (f) => { const st = view.current; st.zoom = Math.max(0.6, Math.min(2.4, st.zoom * f)); st.touched = performance.now() }
  const reset = () => { const st = view.current; st.m = rotation([1, 0, 0], -0.32); st.zoom = 1; st.vx = 0; st.vy = 0; st.focus = null; onPick(null) }

  return (
    <div className="globe" ref={wrap} tabIndex={0} role="application"
      aria-label={`${label}. Drag or use the arrow keys to turn it; plus and minus zoom; Tab moves between items.`}
      onPointerEnter={() => { view.current.hover = true }} onPointerLeave={() => { view.current.hover = false }}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onKeyDown={key}
      onClick={(e) => { if (e.target === e.currentTarget && view.current.moved < 4) onPick(null) }}>
      <svg className="globe-svg" width={size.w} height={size.h} aria-hidden="true">
        <defs>
          <radialGradient id="globe-fill" cx="38%" cy="32%" r="75%">
            <stop offset="0%" stopColor="var(--globe-light)" />
            <stop offset="70%" stopColor="var(--globe-mid)" />
            <stop offset="100%" stopColor="var(--globe-edge)" />
          </radialGradient>
          <radialGradient id="globe-hub">
            <stop offset="0%" stopColor="var(--globe-hub-core)" />
            <stop offset="35%" stopColor="var(--globe-hub)" stopOpacity=".7" />
            <stop offset="100%" stopColor="var(--globe-hub)" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle className="globe-disc" ref={(n) => { grat.current.disc = n }} r="0" fill="url(#globe-fill)" />
        <path className="globe-lines back" ref={(n) => { grat.current.back = n }} />
        <path className="globe-dots back" ref={(n) => { grat.current.dotsBack = n }} />
        <path className="globe-lines" ref={(n) => { grat.current.front = n }} />
        <path className="globe-dots" ref={(n) => { grat.current.dotsFront = n }} />
        {MESH.hubs.map((i, k) => <circle key={i} className="globe-hub" fill="url(#globe-hub)" r="0"
          ref={(n) => { grat.current.hubs[k] = n }} />)}
        {edges.map((e) => {
          const id = `${e.kind}|${e.source}|${e.target}`
          const dim = near && !(near.has(e.source) && near.has(e.target))
          return <path key={id} className={`globe-edge ${e.kind.toLowerCase()} ${dim ? 'dim' : ''} ${near && !dim ? 'lit' : ''}`}
            ref={(n) => { if (n) edgeEls.current.set(id, n); else edgeEls.current.delete(id) }} />
        })}
      </svg>
      {nodes.map((n) => {
        const state = stateOf(n, diff)
        const dim = near && !near.has(n.key)
        return (
          <button key={n.key} type="button" title={n.text} data-side="front"
            ref={(el) => { if (el) els.current.set(n.key, el); else els.current.delete(n.key) }}
            className={`globe-node sphere-node ${n.kind.toLowerCase()} ${n.status === 'proposed' ? 'proposed' : ''} ${state} ${selected === n.key ? 'on' : ''} ${dim ? 'dim' : ''}`}
            onClick={(e) => { e.stopPropagation(); if (view.current.moved < 4) onPick(n.key) }}
            onFocus={() => focusOn(n.key)}
            aria-label={`${n.kind === 'Concept' ? 'Concept' : n.kind === 'Rule' ? 'Business rule' : 'Accepted exception'}${n.status === 'proposed' ? ', proposed' : ''}: ${n.text}`}>
            {n.kind !== 'Concept' && <span className="sphere-node-kind">{n.kind === 'Rule' ? `Rule${n.protects ? ` · ${n.protects}` : ''}` : 'Exception'}{n.status === 'proposed' ? ' · proposed' : ''}</span>}
            <span className="sphere-node-text">{n.text}</span>
          </button>
        )
      })}
      <div className="globe-tools" role="group" aria-label="View">
        <button type="button" className="btn small" aria-label="Zoom in" onClick={(e) => { e.stopPropagation(); zoomBy(1.2) }}>+</button>
        <button type="button" className="btn small" aria-label="Zoom out" onClick={(e) => { e.stopPropagation(); zoomBy(1 / 1.2) }}>−</button>
        <button type="button" className="btn small" aria-label="Reset the view" onClick={(e) => { e.stopPropagation(); reset() }}><Icon.fit size={14} /></button>
      </div>
      <p className="globe-hint" aria-hidden="true">Drag to turn · scroll to zoom · choose an item to bring it forward</p>
    </div>
  )
}
