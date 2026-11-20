// The flat plan view: draws the clone with React Flow and reports what the person did in the clone's own coordinates.
// It is one "surface" (see the boundary note at the top of editor.js). It holds no editing rules: it never decides where a
// dropped item belongs, how a group grows or what is saved. It draws, reads the pointer, and tells the editor.
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Background, Controls, MarkerType, ReactFlow, useReactFlow, useUpdateNodeInternals } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Empty, ErrorNote, Loading } from '../components/ui'
import { GENERIC_RELATION, firstFit, structureKey } from './edit'
import { GROUP_PAD, GROUP_MIN, boxesFromFlow } from './geometry'
import { planFlow, visibleEdges } from './layout'
import { humanKey } from './model'
import { NODE_TYPES } from './nodes'
import { senseWords } from './scene/sensepoints'
import { weaknessWords } from './weaknesses'

// A clone opens fitted whole: names where they fit (studio/nodes.jsx says from how far out), shapes and icons beyond.
// The view keeps more room on top, where the watch panel and the view switch float over the canvas.
const MIN_ZOOM = 0.04
const FIT_VIEW = { padding: { top: '68px', right: '24px', bottom: '24px', left: '24px' }, maxZoom: 1, minZoom: MIN_ZOOM }
const GRID_SIZE = 24
// Below 1280 px the watch panel sits under the view switch (studio.css), so a fitted view leaves room for both rows.
const NARROW_CANVAS = '(max-width: 1279px)'
const fitOptions = () => ({ ...FIT_VIEW, padding: { ...FIT_VIEW.padding, top: window.matchMedia(NARROW_CANVAS).matches ? '140px' : FIT_VIEW.padding.top } })

/** The nodes to draw: the stored places, or the automatic layout when the clone has none. Worked out again only when the shape changes. */
function useCanvasLayout(cloneDoc, types) {
  const key = structureKey(cloneDoc)
  const docRef = useRef(cloneDoc)
  docRef.current = cloneDoc
  const [round, setRound] = useState(0)
  const ready = Boolean(cloneDoc) && types.size > 0           // the layout reads the library, so it waits for it
  const nodes = useMemo(() => (ready ? planFlow(docRef.current, types) : NO_NODES), [key, round, ready, types]) // eslint-disable-line react-hooks/exhaustive-deps
  const relayout = useCallback(() => setRound((value) => value + 1), [])
  return { nodes, relayout, settled: ready }
}

const NO_NODES = []
const NO_POINTS = []

const REMEASURE_TRIES = 120  // about two seconds of frames

export const FlatCanvas = forwardRef(function FlatCanvas({
  document: cloneDoc, current, loading, error, retry, editing, selectedIds, selectedEdge, describeItem, types, events, marks,
  dragRef, onStatus, emptyHint, sensePoints = NO_POINTS, weaknesses, showRelations = false,
}, ref) {
  const flow = useReactFlow()
  const canvas = useRef(null)
  const { nodes, relayout, settled } = useCanvasLayout(cloneDoc, types)
  const laying = !settled && Boolean(cloneDoc)
  const boxes = useMemo(() => boxesFromFlow(nodes), [nodes])
  const boxesRef = useRef(boxes)
  boxesRef.current = boxes
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  const [moved, setMoved] = useState({})
  const [sized, setSized] = useState({})
  const [dropping, setDropping] = useState(false)
  const [connecting, setConnecting] = useState(null)
  const idsRef = useRef(selectedIds)
  idsRef.current = selectedIds
  const fitted = useRef(null)

  useLayoutEffect(() => { events.onBoxes(boxes) }, [boxes, events])
  // React Flow draws a connection only between cards it has measured (their size and handle places). On a busy machine
  // the first measurement can be lost, and nothing asks again: the cards show and their connections never do. So, after
  // cards are drawn, any card React Flow holds no handle places for is measured again, a few frames at a time.
  const remeasure = useUpdateNodeInternals()
  useEffect(() => {
    let frame = 0
    let tries = 0
    const check = () => {
      const unmeasured = nodesRef.current.map((node) => node.id).filter((id) => !flow.getInternalNode(id)?.internals?.handleBounds)
      if (!unmeasured.length || tries >= REMEASURE_TRIES) return
      tries += 1
      remeasure(unmeasured)
      frame = requestAnimationFrame(check)
    }
    frame = requestAnimationFrame(check)
    return () => cancelAnimationFrame(frame)
  }, [nodes, flow, remeasure])
  useEffect(() => { setMoved({}); setSized({}) }, [nodes])
  useEffect(() => { onStatus?.({ laying, count: nodes.length }) }, [laying, nodes.length, onStatus])

  const fit = useCallback(() => flow.fitView({ ...fitOptions(), duration: 0 }), [flow])
  useImperativeHandle(ref, () => ({
    pointAt: (clientX, clientY) => flow.screenToFlowPosition({ x: clientX, y: clientY }),
    viewCentre() {
      const rect = canvas.current.getBoundingClientRect()
      return flow.screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 })
    },
    fit,
    // Fit once every card on the canvas has been drawn and measured: after a bring-in the new cards take a moment, and fitting
    // before then fits only what was measured.
    fitWhenDrawn() {
      let tries = 0
      const wait = () => {
        const drawn = flow.getNodes()
        const ready = drawn.length === nodesRef.current.length && drawn.every((node) => (node.measured?.width ?? node.width) && (node.measured?.height ?? node.height))
        if (ready || tries > 240) {
          fit()
          // What was removed or resized settles a moment later; fit once more so the view matches what is left.
          window.setTimeout(fit, 450)
        } else { tries += 1; requestAnimationFrame(wait) }
      }
      requestAnimationFrame(wait)
    },
    relayout() { fitted.current = null; relayout() },
    // Bring an item into view when it is off screen (a keyboard placement is always near the middle, so this is rare).
    reveal(id) {
      requestAnimationFrame(() => {
        const box = boxesRef.current.get(id)
        const rect = canvas.current?.getBoundingClientRect()
        if (!box || !rect) return
        const { x, y, zoom } = flow.getViewport()
        const inside = box.x * zoom + x >= 0 && box.y * zoom + y >= 0
          && (box.x + box.width) * zoom + x <= rect.width && (box.y + box.height) * zoom + y <= rect.height
        if (!inside) flow.setCenter(box.x + box.width / 2, box.y + box.height / 2, { zoom, duration: 0 })
      })
    },
  }), [fit, flow, relayout])
  // Opening a clone shows all of it. Editing never moves the view: only opening, and Lay out again, do.
  useEffect(() => {
    // The clone's name is part of the key: for a moment after another clone is chosen, `current` is new and the drawing is still the old one's.
    const opened = cloneDoc && `${current}|${cloneDoc.clone}`
    if (!cloneDoc || !settled || fitted.current === opened) return undefined
    fitted.current = opened
    if (!nodes.length) return undefined
    const frame = requestAnimationFrame(() => {
      if (canvas.current) fit()
    })
    return () => cancelAnimationFrame(frame)
  }, [cloneDoc, current, fit, settled, nodes])

  const flowNodes = useMemo(() => {
    const picked = new Set(selectedIds)
    const held = new Map()
    for (const node of nodes) if (node.parentId) held.set(node.parentId, [...(held.get(node.parentId) || []), node.id])
    const origin = connecting && describeItem(connecting)
    const senseOf = new Map(sensePoints.map((point) => [point.node, point]))
    return nodes.map((node) => {
      const { item, type, described, ariaLabel } = describeItem(node.id, node.data.item)
      const problems = marks?.[node.id]
      const own = boxes.get(node.id)
      const inside = (held.get(node.id) || []).map((id) => boxes.get(id)).filter(Boolean)
      const minSize = node.type === 'boundary' ? {
        width: Math.max(GROUP_MIN.width, ...inside.map((box) => box.x + box.width - own.x + GROUP_PAD.side)),
        height: Math.max(GROUP_MIN.height, ...inside.map((box) => box.y + box.height - own.y + GROUP_PAD.bottom)),
      } : undefined
      const link = origin && node.id !== connecting
        ? (firstFit(origin.type, type, { fromId: connecting, toId: node.id, edges: cloneDoc.edges }) === GENERIC_RELATION ? 'generic' : 'typed')
        : null
      const weak = weaknesses?.[node.id]
      return {
        ...node, position: moved[node.id] || node.position, draggable: editing, selected: picked.has(node.id),
        ...(sized[node.id] || {}),
        ariaLabel: `${ariaLabel}${problems ? `, connector problem: ${problems.join(' ')}` : ''}${senseOf.has(node.id) ? `, ${senseWords(senseOf.get(node.id).status).toLowerCase()}` : ''}${weak ? `, ${weaknessWords(weak)}` : ''}`,
        data: { ...node.data, item, type, described, editable: editing, problems, minSize, link, sense: senseOf.get(node.id), weak, onResize: handleResize },
      }
    })
  }, [nodes, selectedIds, moved, sized, editing, marks, boxes, describeItem, connecting, cloneDoc, sensePoints, weaknesses]) // eslint-disable-line react-hooks/exhaustive-deps

  const edges = useMemo(() => {
    const visible = cloneDoc ? visibleEdges(cloneDoc) : []
    const groups = new Map()
    for (const edge of visible) {
      const key = showRelations ? `${edge.from}:${edge.to}` : edge.key
      const group = groups.get(key) || []
      group.push(edge)
      groups.set(key, group)
    }
    return [...groups.values()].map(group => {
      const edge = group[0]
      const on = group.some(item => selectedEdge && item.from === selectedEdge.from &&
        item.to === selectedEdge.to && item.type === selectedEdge.type)
      const nearby = selectedIds.includes(edge.from) || selectedIds.includes(edge.to)
      const nameOf = (id) => describeItem(id)?.item?.name || id
      const label = group.map(item => humanKey(item.type)).join(' / ')
      return {
        id: edge.key, source: edge.from, target: edge.to, selected: on, type: 'smoothstep',
        label: on || showRelations ? label : undefined, data: { edge },
        ariaLabel: `${nameOf(edge.from)} ${label.toLowerCase()} ${nameOf(edge.to)}`,
        ...(showRelations ? {
          markerEnd: { type: MarkerType.ArrowClosed, color: nearby ? 'var(--brand)' : 'var(--faint)' },
          style: { stroke: nearby ? 'var(--brand)' : 'var(--faint)', strokeWidth: nearby ? 2.5 : 1.5 },
          labelStyle: { fill: 'var(--ink-2)', fontWeight: 600 },
        } : {}),
      }
    })
  }, [cloneDoc, describeItem, selectedEdge, selectedIds, showRelations])


  function handleResize(id, box) {
    const parent = nodesRef.current.find((node) => node.id === id)?.parentId
    const base = parent ? boxesRef.current.get(parent) : { x: 0, y: 0 }
    setMoved({}); setSized({})
    events.onResizeEnd(id, { x: base.x + box.x, y: base.y + box.y, width: box.width, height: box.height })
  }
  const onNodesChange = useCallback((changes) => {
    const picks = changes.filter((change) => change.type === 'select')
    if (picks.length) {
      const next = new Set(idsRef.current)
      for (const change of picks) {
        if (change.selected) next.add(change.id)
        else next.delete(change.id)
      }
      const same = next.size === idsRef.current.length && idsRef.current.every((id) => next.has(id))
      if (!same) events.onSelect([...next])
    }
    const places = changes.filter((change) => change.type === 'position' && change.position)
    if (places.length) {
      setMoved((prev) => {
        const next = { ...prev }
        for (const change of places) next[change.id] = change.position
        return next
      })
    }
    const resizing = changes.filter((change) => change.type === 'dimensions' && change.resizing && change.dimensions)
    if (resizing.length) {
      setSized((prev) => {
        const next = { ...prev }
        for (const change of resizing) next[change.id] = { width: change.dimensions.width, height: change.dimensions.height }
        return next
      })
    }
  }, [events])
  const onEdgesChange = useCallback((changes) => {
    const picked = changes.find((change) => change.type === 'select' && change.selected)
    const edge = picked && edges.find((entry) => entry.id === picked.id)
    if (edge) events.onSelectEdge(edge.data.edge)
  }, [edges, events])
  const onNodeDragStop = useCallback((_event, _node, dragged) => {
    const moves = new Map()
    for (const node of dragged) {
      const base = node.parentId ? boxesRef.current.get(node.parentId) : { x: 0, y: 0 }
      const at = { x: base.x + node.position.x, y: base.y + node.position.y }
      const was = boxesRef.current.get(node.id)
      if (was && (was.x !== at.x || was.y !== at.y)) moves.set(node.id, at)
    }
    setMoved({})
    events.onMoveEnd(moves)
  }, [events])

  const onDragOver = (event) => {
    if (!editing || !dragRef.current) return
    event.preventDefault()
    setDropping(true)
  }
  const onDrop = (event) => {
    if (!editing) return
    event.preventDefault()
    const type = dragRef.current
    dragRef.current = null
    setDropping(false)
    if (type) events.onDropType(type, flow.screenToFlowPosition({ x: event.clientX, y: event.clientY }))
  }

  const empty = cloneDoc && !laying && !cloneDoc.nodes.length && !cloneDoc.groups.length
  return (
    <div ref={canvas} className={`studio-canvas ${dropping ? 'drop-target' : ''} ${connecting ? 'connecting' : ''}`} aria-busy={laying} onDragOver={onDragOver} onDrop={onDrop}
      onDragLeave={() => setDropping(false)}>
      {error && <ErrorNote error={error} retry={retry} />}
      {(loading || laying) && !nodes.length && <div className="studio-wait"><Loading rows={4} /><span className="muted">Laying out the estate</span></div>}
      {empty && <div className="studio-wait"><Empty title="This clone is empty">{emptyHint}</Empty></div>}
      <ReactFlow nodes={flowNodes} edges={edges} nodeTypes={NODE_TYPES} minZoom={MIN_ZOOM} maxZoom={2} fitViewOptions={fitOptions()}
        nodesDraggable={editing} nodesConnectable={editing} elementsSelectable deleteKeyCode={null} connectionRadius={36}
        multiSelectionKeyCode={['Shift', 'Meta', 'Control']} snapToGrid={false}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeDragStop={onNodeDragStop}
        onConnect={(connection) => events.onConnect(connection.source, connection.target)}
        onConnectStart={(_event, params) => setConnecting(params.nodeId)} onConnectEnd={() => setConnecting(null)}
        onPaneClick={() => events.onBlank()} attributionPosition="bottom-left"
        aria-label={`Canvas of ${cloneDoc?.clone || 'the clone'}`}>
        <Background gap={GRID_SIZE} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  )
})
