import { useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from '../components/ui'

// KS.1: the knowledge sphere as a turning 3D network (no globe drawn: the points alone make the shape). Concepts are spread over its surface, related ones side by side; each
// concept's rules and exceptions sit in a small ring round it, coloured by what breaking them costs; links are lines.
// It never turns by itself: only dragging turns it (sideways spins, up and down tilts). Scrolling zooms, choosing an item
// lights it and what it touches. Plain maths on SVG: no 3D library.

const COST = { money: '#c2410c', data: '#0f766e', access: '#6d28d9', availability: '#b45309', other: '#64748b' }
const COST_WORDS = { money: 'Money', data: 'Data', access: 'Access', availability: 'Availability', other: 'Other' }
const R = { Concept: 14, Rule: 7, Exception: 8 }
const SPHERE = 300 // the globe's radius, in its own units; the view scales it to the panel
const CAMERA = 1400 // distance of the eye: a soft perspective

const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s]
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const norm = (a) => { const l = Math.hypot(...a) || 1; return scale(a, 1 / l) }

function colourOf(node, homes, byKey) {
  if (node.kind === 'Concept') return null
  if (node.kind === 'Exception') return '#d97706'
  return COST[node.protects] || COST[byKey[homes[node.key]]?.protects] || COST.other
}

/** `count` points spread evenly over the sphere (a Fibonacci lattice), starting at the front. */
function spread(count) {
  if (count === 1) return [[0, 0, 1]]
  const golden = Math.PI * (3 - Math.sqrt(5))
  return Array.from({ length: count }, (_, i) => {
    const y = 1 - (i / (count - 1)) * 2
    const r = Math.sqrt(1 - y * y)
    return [Math.sin(golden * i) * r, y * 0.92, Math.cos(golden * i) * r]
  }).map(norm)
}

/** Where everything sits on the unit sphere. The same sphere always lands the same way. */
function layout(nodes, edges) {
  const homes = {}
  for (const e of edges) if (e.kind === 'ABOUT' && !homes[e.source]) homes[e.source] = e.target
  for (const e of edges) if (e.kind === 'EXEMPTS' && !homes[e.source] && homes[e.target]) homes[e.source] = homes[e.target]
  const concepts = nodes.filter((n) => n.kind === 'Concept')
  const members = Object.fromEntries(concepts.map((c) => [c.key, []]))
  const loose = []
  for (const n of nodes) {
    if (n.kind === 'Concept') continue
    if (members[homes[n.key]]) members[homes[n.key]].push(n)
    else loose.push(n)
  }
  // Related concepts next to each other: walk from the busiest one to its unvisited neighbours.
  const links = {}
  for (const e of edges) {
    if (e.kind !== 'RELATES') continue
    ;(links[e.source] = links[e.source] || []).push(e.target)
    ;(links[e.target] = links[e.target] || []).push(e.source)
  }
  const order = []
  const left = new Set(concepts.map((c) => c.key))
  while (left.size) {
    let next = [...left].sort((a, b) => members[b].length - members[a].length)[0]
    while (next && left.has(next)) {
      order.push(next)
      left.delete(next)
      next = (links[next] || []).find((k) => left.has(k))
    }
  }
  const anchors = spread(order.length + loose.length)
  const dir = {}
  const ring = nodes.length > 80 ? 0.2 : 0.34 // radians from a concept to its rules
  order.forEach((key, i) => {
    const c = anchors[i]
    dir[key] = c
    const up = Math.abs(c[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0]
    const u = norm(cross(up, c))
    const v = cross(c, u)
    const list = members[key]
    list.forEach((n, j) => {
      const outer = j >= 8 ? 1 : 0 // a second ring for a busy concept
      const inRing = outer ? list.length - 8 : Math.min(8, list.length)
      const a = ((outer ? j - 8 : j) / inRing) * Math.PI * 2 + outer * 0.4
      const off = ring * (1 + outer * 0.6)
      dir[n.key] = norm(add(scale(c, Math.cos(off)), add(scale(u, Math.sin(off) * Math.cos(a)), scale(v, Math.sin(off) * Math.sin(a)))))
    })
  })
  loose.forEach((n, i) => { dir[n.key] = anchors[order.length + i] })
  return { dir, homes }
}

export default function SphereGraph({ nodes, edges, diff, selected, onPick, stateOf, label }) {
  const wrap = useRef(null)
  const [size, setSize] = useState({ w: 900, h: 620 })
  const [zoom, setZoom] = useState(1)
  const [turn, setTurn] = useState({ spin: 0.3, tilt: -0.25 })
  const [hover, setHover] = useState(null)
  const drag = useRef(null)
  const still = useRef(typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
  const byKey = useMemo(() => Object.fromEntries(nodes.map((n) => [n.key, n])), [nodes])
  // The page asks the server again every few seconds; the layout follows what the sphere holds, not each answer.
  const shape = useMemo(() => JSON.stringify([nodes.map((n) => n.key).sort(), edges.map((e) => `${e.kind}|${e.source}|${e.target}`).sort()]),
    [nodes, edges])
  const { dir, homes } = useMemo(() => layout(nodes, edges), [shape]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!wrap.current) return undefined
    const seen = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }))
    seen.observe(wrap.current)
    return () => seen.disconnect()
  }, [])

  // The globe fills the panel, with room on both sides for the words beside the dots.
  const few = nodes.length <= 80
  const room = few ? Math.min(240, size.w * 0.22) : 70
  const fitted = Math.max(0.3, Math.min((size.w - 2 * room) / (2.15 * SPHERE), (size.h - 120) / (2.15 * SPHERE)))
  const k = fitted * zoom
  const cx = size.w / 2
  const cy = (size.h - 34) / 2
  const letters = Math.max(14, Math.min(56, Math.floor((room - 30) / 6.2)))

  const cs = Math.cos(turn.spin)
  const sn = Math.sin(turn.spin)
  const ct = Math.cos(turn.tilt)
  const st = Math.sin(turn.tilt)
  /** Where a point of the globe shows on screen, and how near it is (-1 the far side, 1 the front). */
  const project = (v) => {
    const x1 = v[0] * cs + v[2] * sn
    const z1 = -v[0] * sn + v[2] * cs
    const y2 = v[1] * ct - z1 * st
    const z2 = v[1] * st + z1 * ct
    const p = CAMERA / (CAMERA - z2 * SPHERE)
    return { x: cx + x1 * SPHERE * k * p, y: cy - y2 * SPHERE * k * p, z: z2, scale: p }
  }
  const placed = useMemo(() => Object.fromEntries(Object.entries(dir).map(([key, v]) => [key, project(v)])),
    [dir, turn, k, cx, cy]) // eslint-disable-line react-hooks/exhaustive-deps

  const focus = selected || hover
  const near = useMemo(() => {
    if (!focus) return null
    const out = new Set([focus])
    for (const e of edges) {
      if (e.source === focus) out.add(e.target)
      if (e.target === focus) out.add(e.source)
    }
    return out
  }, [focus, edges])

  // Dragging turns the globe; a press that hardly moves is a click on whatever is under it.
  const down = (e, key = null) => {
    if (e.button !== 0) return
    e.stopPropagation()
    drag.current = { key, x: e.clientX, y: e.clientY, moved: 0 }
    wrap.current.setPointerCapture?.(e.pointerId)
  }
  const move = (e) => {
    const d = drag.current
    if (!d) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    d.x = e.clientX
    d.y = e.clientY
    d.moved += Math.abs(dx) + Math.abs(dy)
    if (d.moved < 4) return
    const per = 1 / (SPHERE * k)
    setTurn((t) => ({ spin: t.spin + dx * per, tilt: Math.max(-1.2, Math.min(1.2, t.tilt + dy * per)) }))
  }
  const up = () => {
    const d = drag.current
    drag.current = null
    if (d && d.moved < 4) onPick(d.key)
  }
  useEffect(() => {
    const el = wrap.current
    if (!el) return undefined
    const wheel = (e) => {
      e.preventDefault()
      setZoom((z) => Math.max(0.5, Math.min(3, z * Math.exp(-e.deltaY * 0.0015))))
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => el.removeEventListener('wheel', wheel)
  }, [])
  const keyTurn = (e) => {
    const step = 0.15
    const by = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key]
    if (by) {
      e.preventDefault()
      setTurn((t) => ({ spin: t.spin + by[0], tilt: Math.max(-1.2, Math.min(1.2, t.tilt + by[1])) }))
    } else if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(3, z * 1.2))
    else if (e.key === '-') setZoom((z) => Math.max(0.5, z / 1.2))
  }

  const costs = [...new Set(nodes.filter((n) => n.kind === 'Rule').map((n) => n.protects || 'other'))]
  const short = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text)
  const order = [...nodes].filter((n) => placed[n.key]).sort((a, b) => placed[a.key].z - placed[b.key].z)
  return (
    <div className="kgraph" ref={wrap} tabIndex={0} role="application" onKeyDown={keyTurn}
      aria-label={`${label}. Drag or use the arrow keys to turn it; plus and minus zoom; Tab moves between items.`}
      onPointerDown={(e) => down(e)} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { drag.current = null }}>
      <svg className="kgraph-svg" width={size.w} height={size.h}>
        <defs>
          <radialGradient id="kgraph-glow">
            <stop offset="0%" stopColor="var(--brand)" stopOpacity=".28" />
            <stop offset="100%" stopColor="var(--brand)" stopOpacity="0" />
          </radialGradient>
        </defs>
        {edges.map((e) => {
          const a = placed[e.source]
          const b = placed[e.target]
          if (!a || !b) return null
          const lit = near && near.has(e.source) && near.has(e.target)
          const tone = colourOf(byKey[e.source] || {}, homes, byKey) || 'var(--brand)'
          const behind = (a.z + b.z) / 2 < -0.15
          return <line key={`${e.kind}|${e.source}|${e.target}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
            className={`kgraph-edge ${e.kind.toLowerCase()} ${lit ? 'lit' : ''} ${near && !lit ? 'dim' : ''} ${behind ? 'behind' : ''}`}
            stroke={e.kind === 'RELATES' ? 'var(--brand)' : tone} />
        })}
        {order.map((n) => {
          const p = placed[n.key]
          const state = stateOf(n, diff)
          const isOn = selected === n.key
          const dim = near && !near.has(n.key)
          const fill = colourOf(n, homes, byKey)
          const front = p.z > -0.1
          const depth = Math.max(0.18, Math.min(1, 0.6 + p.z * 0.45)) // the far side fades
          // Words on the near side, pointing away from the middle so they never cross the globe; a concept always.
          const showLabel = isOn || hover === n.key || (n.kind === 'Concept' ? p.z > -0.55 : few && front && !dim)
          const leftward = p.x < cx
          return (
            <g key={n.key} transform={`translate(${p.x},${p.y})`} tabIndex={0} role="button" style={{ opacity: dim ? undefined : depth }}
              className={`kgraph-node ${n.kind.toLowerCase()} ${n.status === 'proposed' ? 'proposed' : ''} ${state} ${isOn ? 'on' : ''} ${dim ? 'dim' : ''}`}
              aria-label={`${n.kind === 'Concept' ? 'Concept' : n.kind === 'Rule' ? 'Business rule' : 'Accepted exception'}${n.status === 'proposed' ? ', proposed' : ''}: ${n.text}`}
              onPointerDown={(e) => down(e, n.key)} onPointerEnter={() => setHover(n.key)} onPointerLeave={() => setHover(null)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(n.key) } }}>
              {(n.kind === 'Concept' || isOn) && front && <circle r={R[n.kind] * 2.6 * p.scale} fill="url(#kgraph-glow)" />}
              <circle r={(R[n.kind] + (isOn ? 2 : 0)) * p.scale} fill={fill || 'var(--surface)'} stroke={fill || 'var(--brand)'} className="kgraph-dot" />
              {showLabel && (
                <text className="kgraph-label" x={leftward ? -(R[n.kind] + 6) : R[n.kind] + 6} y={4} textAnchor={leftward ? 'end' : 'start'}>
                  {n.kind === 'Concept' || isOn || hover === n.key ? n.text : short(n.text, letters)}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <div className="globe-tools" role="group" aria-label="View">
        <button type="button" className="btn small" aria-label="Zoom in" onPointerDown={(e) => e.stopPropagation()} onClick={() => setZoom((z) => Math.min(3, z * 1.2))}>+</button>
        <button type="button" className="btn small" aria-label="Zoom out" onPointerDown={(e) => e.stopPropagation()} onClick={() => setZoom((z) => Math.max(0.5, z / 1.2))}>−</button>
        <button type="button" className="btn small" aria-label="Reset the view" onPointerDown={(e) => e.stopPropagation()}
          onClick={() => { setZoom(1); setTurn({ spin: 0.3, tilt: -0.25 }); onPick(null) }}><Icon.fit size={14} /></button>
      </div>
      <div className="kgraph-legend" aria-label="Colours">
        <span><i className="concept" />Concept</span>
        {costs.map((c) => <span key={c}><i style={{ background: COST[c] || COST.other }} />{COST_WORDS[c] || c} rule</span>)}
        {nodes.some((n) => n.kind === 'Exception') && <span><i style={{ background: '#d97706' }} />Accepted exception</span>}
      </div>
      <p className="globe-hint" aria-hidden="true">Drag to turn · scroll to zoom · choose an item to see what it touches</p>
    </div>
  )
}
