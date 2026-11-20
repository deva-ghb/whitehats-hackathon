// The isometric scene: draws the clone like the website's hero picture and reports what the person did in the clone's own
// ground coordinates. It is the second "surface" (see the boundary note at the top of editor.js): it holds no editing rules.
// It turns the pointer into a ground point, draws the boxes it is given, and tells the editor what happened.
//   useSceneView   where the view is, and the pointer to ground point
//   useSceneInput  press, drag, keys and drops, and the state they leave on the drawing
//   buildScene     what is drawn (model.js);  Layers.jsx the parts it is drawn from;  Overlays.jsx what sits over it
import { forwardRef, useImperativeHandle, useLayoutEffect, useEffect, useMemo, useRef } from 'react'
import { Empty, ErrorNote, Loading } from '../../components/ui'
import { GENERIC_RELATION, firstFit } from '../edit'
import { STATE_LABEL, humanKey } from '../model'
import { placeLabels, shortName } from './labels'
import { Defs, EdgeShape, Label, PlatformBack, PlatformFront, SenseShape, SolidShape, ZoneFloor, ZoneRim, platformLabelPlan } from './Layers'
import { buildScene } from './model'
import { AlertMarks, Controls, SceneKey, Tooltip, UiLayer } from './Overlays'
import { COS30, SCALE, UNIT_X, UNIT_Y, project, sceneToScreen } from './project'
import { sceneBoxes } from './placement'
import { senseWords, statesFor } from './sensepoints'
import { reservedRects } from './reserved'
import { useOpeningFit, useSceneView } from './useSceneView'
import { useSceneInput } from './useSceneInput'
import './scene.css'

const ZOOM_DETAIL = 0.3       // below this the scene draws plain boxes
const ZOOM_LABELS = 0.3       // below this it shows no names (except the selected one)
const ZOOM_SUB = 1.6          // from here a name carries its type underneath
const ZERO = [0, 0]
/** Where a sense point sits: on top of a solid, or on the ground spot under a person. */
const senseSpot = (thing) => (thing ? (thing.floats ? thing.bottom : thing.top) : null)

const plainWords = (type) => {
  const reason = type?.default_sense_points?.[0]?.reason
  if (!reason) return ''
  return reason.endsWith('.') ? reason : `${reason}.`
}

const NO_POINTS = []

export const IsoCanvas = forwardRef(function IsoCanvas({
  document: cloneDoc, current, loading, error, retry, editing, selectedIds, selectedEdge, describeItem, types, events, marks,
  dragRef, onStatus, emptyHint, sensePoints = NO_POINTS, findings,
}, ref) {
  const sceneRef = useRef(null)
  const tipRef = useRef(null)
  const view = useSceneView({ sceneRef })
  const { settled, size } = view
  const detail = settled.zoom >= ZOOM_DETAIL

  const states = useMemo(() => statesFor(sensePoints, findings), [sensePoints, findings])
  const boxes = useMemo(() => sceneBoxes(cloneDoc, types), [cloneDoc, types])
  const scene = useMemo(() => buildScene({ document: cloneDoc, types, states, detail, boxes }), [cloneDoc, types, states, detail, boxes])
  sceneRef.current = scene
  const picked = useMemo(() => new Set(selectedIds), [selectedIds])
  // The things whose names matter most: the one with the most connections, and everything that has a sense point or a finding.
  const keyed = useMemo(() => new Set([...scene.solids.filter((solid) => solid.isTower).map((solid) => solid.id), ...states.keys()]), [scene, states])
  const empty = Boolean(cloneDoc && !cloneDoc.nodes.length && !cloneDoc.groups.length)
  const byId = useMemo(() => new Map([...scene.zones, ...scene.solids, ...scene.floats].map((thing) => [thing.id, thing])), [scene])
  const byIdRef = useRef(byId)
  byIdRef.current = byId
  const idsRef = useRef(selectedIds)
  idsRef.current = selectedIds
  const docRef = useRef(cloneDoc)
  docRef.current = cloneDoc

  const input = useSceneInput({ view, sceneRef, byIdRef, docRef, idsRef, editing, events, dragRef, tipRef })
  const { hover, focusId, moved, ghost, connecting, resizing, marquee, panning, overItem, dropping } = input.state
  // A map nobody edits by hand is drawn by its connections: while the person has not moved the view, it keeps the whole map in sight.
  const boundsKey = scene.bounds ? [scene.bounds.x, scene.bounds.y, scene.bounds.width, scene.bounds.height].map(Math.round).join(',') : ''
  useOpeningFit({ view, current, cloneDoc, hasBounds: Boolean(scene.bounds), boundsKey, empty, followGrowth: !editing })

  useLayoutEffect(() => { events.onBoxes(scene.boxes) }, [scene.boxes, events])
  useEffect(() => { onStatus?.({ laying: false, count: byId.size }) }, [byId, onStatus])

  useImperativeHandle(ref, () => ({
    pointAt: view.pointAt,
    viewCentre() {
      const rect = view.root.current.getBoundingClientRect()
      return view.pointAt(rect.left + rect.width / 2, rect.top + rect.height / 2)
    },
    fit: view.fit,
    relayout() { view.fit() },
    // Bring an item into view when it is off screen.
    reveal(id) {
      requestAnimationFrame(() => {
        const thing = byIdRef.current.get(id)
        if (!thing) return
        const canvas = view.sizeRef.current
        const now = view.viewRef.current
        const cx = thing.bounds.x + thing.bounds.width / 2
        const cy = thing.bounds.y + thing.bounds.height / 2
        const [sx, sy] = sceneToScreen(now, cx, cy)
        if (sx > 40 && sy > 40 && sx < canvas.width - 40 && sy < canvas.height - 40) return
        view.applyView({ zoom: now.zoom, x: canvas.width / 2 - cx * now.zoom, y: canvas.height / 2 - cy * now.zoom })
      })
    },
  }), [view])

  // What each thing says to a screen reader, and which ends a connection being drawn may take.
  const words = useMemo(() => {
    const out = new Map()
    for (const thing of byId.values()) {
      const problems = marks?.[thing.id]
      const sense = sensePoints.find((point) => point.node === thing.id)
      out.set(thing.id, `${describeItem(thing.id, thing.item).ariaLabel}${problems ? `, connector problem: ${problems.join(' ')}` : ''}${sense ? `, ${senseWords(sense.status).toLowerCase()}` : ''}`)
    }
    return out
  }, [byId, describeItem, marks, sensePoints])
  const links = useMemo(() => {
    if (!connecting || !cloneDoc) return null
    const origin = describeItem(connecting.from)
    const out = new Map()
    for (const thing of byId.values()) {
      if (thing.id === connecting.from) continue
      const kind = firstFit(origin.type, thing.type || describeItem(thing.id).type, { fromId: connecting.from, toId: thing.id, edges: cloneDoc.edges })
      out.set(thing.id, kind === GENERIC_RELATION ? 'generic' : 'typed')
    }
    return out
  }, [connecting?.from, byId, cloneDoc, describeItem]) // eslint-disable-line react-hooks/exhaustive-deps

  const platformLabel = cloneDoc ? `${cloneDoc.clone} · ${cloneDoc.environment}`.toUpperCase() : ''
  const platformName = useMemo(() => platformLabelPlan(scene.platform, platformLabel, settled.zoom), [scene.platform, platformLabel, settled.zoom])

  // Names: the most important first, each in the nearest place that covers no other name, solid, person badge or sense point.
  const wanted = useMemo(() => {
    const rank = (thing) => (picked.has(thing.id) ? 0 : keyed.has(thing.id) ? 1 : thing.isZone || thing.floats ? 2 : 3)
    const list = [...[...scene.zones].reverse().map((zone) => ({ id: zone.id, anchor: [zone.label.x, zone.label.y], text: shortName(zone.item.name), zone: true, reach: { left: zone.box.height / UNIT_Y * COS30 * SCALE, right: zone.box.width / UNIT_X * COS30 * SCALE }, must: picked.has(zone.id), rank: rank(zone) })),
      ...[...[...scene.solids].reverse(), ...scene.floats].map((thing) => ({ id: thing.id, anchor: thing.nameAt, own: thing.bounds, text: thing.shortName, must: picked.has(thing.id), sub: picked.has(thing.id) || settled.zoom >= ZOOM_SUB, rank: rank(thing) }))]
    list.sort((a, b) => a.rank - b.rank)
    const obstacles = []
    const toScreen = (polygon) => polygon.map(([x, y]) => sceneToScreen(settled, x, y))
    const onScreen = (rect) => !(rect.x > size.width + 60 || rect.y > size.height + 60 || rect.x + rect.width < -60 || rect.y + rect.height < -60)
    for (const thing of [...scene.solids, ...scene.floats]) {
      const polygon = toScreen(thing.floats ? thing.badge : thing.outline)
      const xs = polygon.map((p) => p[0])
      const ys = polygon.map((p) => p[1])
      const rect = { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) }
      if (onScreen(rect)) obstacles.push({ id: thing.id, kind: thing.floats ? 'float' : 'solid', rect, polygon })
    }
    if (platformName.fontPx) {
      // The platform's own name lies along its front left face; nothing else's name may sit on it.
      const [sx, sy] = sceneToScreen(settled, ...platformName.from)
      const w = platformName.width
      const f = platformName.fontPx
      const polygon = [[sx, sy - f], [sx + COS30 * w, sy + 0.5 * w - f], [sx + COS30 * w, sy + 0.5 * w], [sx, sy]]
      obstacles.push({ id: 'platform-name', kind: 'float', rect: { x: sx, y: sy - f, width: COS30 * w, height: 0.5 * w + f }, polygon })
    }
    // the panels floating over the scene: no name goes under one, and no leader line runs beneath one
    reservedRects(view.root.current).forEach((rect, i) => obstacles.push({ id: `ui-${i}`, kind: 'ui', rect }))
    for (const point of sensePoints) {
      const at = senseSpot(byId.get(point.node))
      if (!at) continue
      const [sx, sy] = sceneToScreen(settled, at[0], at[1])
      obstacles.push({ id: `sense-${point.id}`, kind: 'sense', rect: { x: sx - 14 * settled.zoom, y: sy - 36 * settled.zoom, width: 56 * settled.zoom, height: 50 * settled.zoom } })
    }
    return placeLabels(settled, size, list, { min: ZOOM_LABELS, obstacles })
  }, [scene, settled, size, picked, keyed, sensePoints, byId, platformName, view.root])

  const offsetOf = (id) => {
    const delta = moved?.get(id)
    return delta ? project(delta[0] / UNIT_X, delta[1] / UNIT_Y, 0) : ZERO
  }
  const nameOf = (id) => describeItem(id)?.item?.name || id
  const edgeLabel = (edge) => `${nameOf(edge.from)} ${humanKey(edge.edge.type).toLowerCase()} ${nameOf(edge.to)}`
  const isChosen = (edge) => selectedEdge && edge.edge.from === selectedEdge.from && edge.edge.to === selectedEdge.to && edge.edge.type === selectedEdge.type

  // Connections run along the ground, under the solids. The ones that touch what is selected or hovered are drawn over them.
  const touching = useMemo(() => new Set([...picked, ...(hover?.id ? [hover.id] : [])]), [picked, hover?.id])
  const related = useMemo(() => new Set(scene.edges.filter((edge) => isChosen(edge) || touching.has(edge.from) || touching.has(edge.to)).map((edge) => edge.id)),
    [scene.edges, touching, selectedEdge]) // eslint-disable-line react-hooks/exhaustive-deps
  const renderEdge = (edge, high) => {
    const a = scene.anchors.get(edge.from)
    const b = scene.anchors.get(edge.to)
    const [ax, ay] = offsetOf(edge.from)
    const [bx, by] = offsetOf(edge.to)
    return <EdgeShape key={edge.id} edge={edge} from={[a[0] + ax, a[1] + ay]} to={[b[0] + bx, b[1] + by]} selected={Boolean(isChosen(edge))} related={high} ariaLabel={edgeLabel(edge)} />
  }
  const solidShape = (solid, offset) => (
    <SolidShape key={solid.id} solid={solid} offset={offset} ariaLabel={words.get(solid.id)} selected={picked.has(solid.id)} plain={!detail} link={links?.get(solid.id)} />
  )
  const labelAt = (thing) => {
    const [dx, dy] = offsetOf(thing.id)
    if (thing.isZone) return [thing.label.x, thing.label.y]
    return [thing.nameAt[0] + dx, thing.nameAt[1] + dy]
  }
  const senseAt = (point) => {
    const at = senseSpot(byId.get(point.node))
    if (!at) return null
    const [dx, dy] = offsetOf(point.node)
    return [at[0] + dx, at[1] + dy]
  }

  const lifted = moved ? scene.solids.filter((solid) => moved.has(solid.id)) : []
  const lying = moved ? scene.solids.filter((solid) => !moved.has(solid.id)) : scene.solids
  const hovered = hover?.id && !picked.has(hover.id) ? byId.get(hover.id) : null
  const selected = [...picked].map((id) => byId.get(id)).filter(Boolean)
  const single = selected.length === 1 && editing ? selected[0] : null
  input.refreshHandles()

  const card = (() => {
    if (!hover || input.session.current?.kind === 'move') return null
    if (hover.edge) return { title: 'Connection', name: edgeLabel(hover.edge), words: '', state: '' }
    const thing = byId.get(hover.id)
    if (!thing) return null
    const sense = sensePoints.find((point) => point.node === hover.id)
    const missing = thing.described?.missing?.length ? ` Needs ${thing.described.missing.map(humanKey).join(', ')}.` : ''
    const state = thing.described ? `${STATE_LABEL[thing.described.state]}.${missing}` : ''
    const links = thing.degree ? ` ${thing.degree} ${thing.degree === 1 ? 'connection' : 'connections'}.` : ''
    const hub = thing.isTower ? ' The most connected solid in this clone, so it stands highest.' : ''
    return { title: thing.typeName, name: thing.item.name, words: plainWords(thing.type), state: `${state}${links}${hub}${sense ? ` ${senseWords(sense.status)}.` : ''}`.trim() }
  })()

  return (
    <div ref={view.root} className={`studio-canvas iso-scene ${dropping ? 'drop-target' : ''} ${connecting ? 'connecting' : ''}`} role="application"
      aria-label={`Isometric scene of ${cloneDoc?.clone || 'the clone'}`} aria-busy={loading} {...input.dropProps}>
      {error && <ErrorNote error={error} retry={retry} />}
      {loading && !cloneDoc && <div className="studio-wait"><Loading rows={4} /><span className="muted">Drawing the estate</span></div>}
      {empty && <div className="studio-wait"><Empty title="This clone is empty">{emptyHint}</Empty></div>}
      <svg ref={view.svg} className={`sc-svg ${detail ? '' : 'plain'} ${editing ? 'editing' : ''} ${overItem ? 'over-item' : ''} ${panning ? 'panning' : ''} ${connecting ? 'connecting' : ''}`}
        tabIndex={-1} {...input.svgProps}>
        <Defs />
        <g ref={view.world} className="sc-world">
          <PlatformBack platform={scene.platform} />
          <g>{scene.zones.map((zone) => <ZoneFloor key={zone.id} zone={zone} offset={offsetOf(zone.id)} ariaLabel={words.get(zone.id)} selected={picked.has(zone.id)} />)}</g>
          <g>{lying.map((solid) => solidShape(solid, ZERO))}</g>
          <g>{scene.edges.filter((edge) => !related.has(edge.id)).map((edge) => renderEdge(edge, false))}</g>
          <g>{scene.floats.map((solid) => solidShape(solid, offsetOf(solid.id)))}</g>
          <g>{lifted.map((solid) => solidShape(solid, offsetOf(solid.id)))}</g>
          <g>{scene.edges.filter((edge) => related.has(edge.id)).map((edge) => renderEdge(edge, true))}</g>
          <g>{scene.zones.map((zone) => <ZoneRim key={zone.id} zone={zone} offset={offsetOf(zone.id)} />)}</g>
          <PlatformFront platform={scene.platform} label={platformLabel} fontPx={platformName.fontPx} />
          <g>{sensePoints.map((point) => {
            const at = senseAt(point)
            return at ? <SenseShape key={point.id} at={at} status={point.status} /> : null
          })}</g>
          <UiLayer things={byId} offsetOf={offsetOf} hovered={hovered} selected={selected} focused={focusId ? byId.get(focusId) : null} links={links}
            ghost={ghost} resizing={resizing} connecting={connecting} single={single} handles={input.handles.current} />
          <AlertMarks marks={marks} things={byId} />
          <g>{[...byId.values()].filter((thing) => wanted.has(thing.id)).map((thing) => (
            <Label key={thing.id} place={wanted.get(thing.id)} at={labelAt(thing)} text={thing.isZone ? shortName(thing.item.name) : thing.shortName}
              sub={!thing.isZone && (picked.has(thing.id) || settled.zoom >= ZOOM_SUB) ? thing.typeName : null} zone={thing.isZone} selected={picked.has(thing.id)} />
          ))}</g>
        </g>
        {marquee && <rect className="sc-marquee" x={marquee.x} y={marquee.y} width={marquee.width} height={marquee.height} />}
      </svg>
      {card && hover && <Tooltip card={card} at={hover} size={size} tipRef={tipRef} />}
      <SceneKey wide={size.width >= 900} />
      <Controls onZoom={view.zoomBy} onFit={view.fit} />
    </div>
  )
})
