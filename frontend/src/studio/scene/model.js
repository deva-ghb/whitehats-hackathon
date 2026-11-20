// What the scene draws, worked out from the clone document: every solid, zone and connection with its place, its outline
// for hit testing, and the order to paint them in. Pure, so it tests without a browser.
import { NODE_H, NODE_W, parentOf } from '../geometry.js'
import { STRUCTURAL, accessibleName, describe } from '../model.js'
import { boundsOfPolygon, hull, rectsOverlap } from './hit.js'
import { rectHitsPolygon, shortName } from './labels.js'
import { BASE, SCALE, UNIT_X, UNIT_Y, groundToScene, project } from './project.js'
import { CELL, LIFT, SPECS, recipeName, solid, toneFor } from './recipes.js'
import { platformBox, sceneBoxes } from './placement.js'

export const ZONE_WALL = 0.6        // height of the glass wall of a zone, in drawing units

const typeIdOf = (item, types) => types.get(item.type)?.id || String(item.type || '').split('@')[0]

/** The connections that are drawn. Nesting already shows a container, so `contained_in` is left out. */
export function drawnEdges(document) {
  const location = new Map((document.nodes || []).map((node) => [node.id, node.location]))
  return (document.edges || [])
    .filter((edge) => !(edge.type === STRUCTURAL && location.get(edge.from) === edge.to))
    .map((edge, i) => ({ ...edge, key: `e${i}-${edge.from}-${edge.type}-${edge.to}` }))
}

/** Rank for painting: A goes before B when A is wholly behind B along x or along y. Rectangles are in drawing units. */
function behind(a, b) {
  return a.x1 <= b.x0 + 1e-6 || a.y1 <= b.y0 + 1e-6
}

/**
 * Paint order back to front for solids standing on one plane. Each has `foot` {x0, x1, y0, y1} in units and `bounds`
 * (screen rectangle). Where two outlines overlap on screen the one wholly behind the other along x or y goes first.
 */
export function paintOrder(things) {
  const n = things.length
  const after = Array.from({ length: n }, () => [])
  const waiting = new Array(n).fill(0)
  for (let i = 0; i < n; i += 1) {
    for (let j = i + 1; j < n; j += 1) {
      const a = things[i]
      const b = things[j]
      if (a.bounds.x > b.bounds.x + b.bounds.width || b.bounds.x > a.bounds.x + a.bounds.width) continue
      if (a.bounds.y > b.bounds.y + b.bounds.height || b.bounds.y > a.bounds.y + a.bounds.height) continue
      let first = null
      if (behind(a.foot, b.foot)) first = i
      else if (behind(b.foot, a.foot)) first = j
      if (first === null) continue
      const second = first === i ? j : i
      after[first].push(second)
      waiting[second] += 1
    }
  }
  const key = (t) => t.foot.x0 + t.foot.y0 + t.foot.x1 + t.foot.y1
  const ready = []
  for (let i = 0; i < n; i += 1) if (waiting[i] === 0) ready.push(i)
  const out = []
  const taken = new Set()
  const pickNext = () => {
    let best = 0
    for (let k = 1; k < ready.length; k += 1) if (key(things[ready[k]]) < key(things[ready[best]])) best = k
    return ready.splice(best, 1)[0]
  }
  while (out.length < n) {
    if (!ready.length) {
      // A loop (it cannot happen for boxes that do not overlap): let the one nearest the back go.
      let best = -1
      for (let i = 0; i < n; i += 1) if (!taken.has(i) && (best < 0 || key(things[i]) < key(things[best]))) best = i
      waiting[best] = 0
      ready.push(best)
    }
    const next = pickNext()
    if (taken.has(next)) continue
    taken.add(next)
    out.push(things[next])
    for (const later of after[next]) {
      waiting[later] -= 1
      if (waiting[later] === 0 && !taken.has(later)) ready.push(later)
    }
  }
  return out
}

const TOWER_MIN_DEGREE = 3        // the tower is the busiest node, and only when it takes part in at least this many connections
const TALL_FROM = 8               // a clone of fewer nodes stands all its solids at the same height
const LIFTS = [LIFT, LIFT + 0.8, LIFT + 1.6, LIFT + 2.4, LIFT + 3.2, LIFT + 4]

/** How many connections each node takes part in. */
function degreesOf(document) {
  const degree = new Map()
  for (const edge of drawnEdges(document)) {
    degree.set(edge.from, (degree.get(edge.from) || 0) + 1)
    degree.set(edge.to, (degree.get(edge.to) || 0) + 1)
  }
  return degree
}

/** The busiest nodes: a deep teal tower for the very busiest, and taller solids for the next busiest. Only solids (not people). */
function busiest(document, types, boxes) {
  const degree = degreesOf(document)
  const out = { degree, tower: null, tall: new Map() }
  if (document.nodes.length < TALL_FROM) return out
  const eligible = document.nodes.filter((node) => {
    const type = types.get(node.type)
    const spec = type && SPECS[recipeName(typeIdOf(node, types), type.family)]
    return boxes.has(node.id) && spec && spec.h && !spec.base && spec.kind !== 'person' && !spec.ghost && (degree.get(node.id) || 0) >= 2
  }).sort((a, b) => (degree.get(b.id) - degree.get(a.id)) || String(a.id).localeCompare(String(b.id)))
  if (eligible.length && degree.get(eligible[0].id) >= TOWER_MIN_DEGREE) out.tower = eligible[0].id
  const top = Math.max(1, Math.round(eligible.length * 0.15))
  const next = Math.max(1, Math.round(eligible.length * 0.3))
  eligible.forEach((node, i) => { if (i < top) out.tall.set(node.id, 1.45); else if (i < top + next) out.tall.set(node.id, 1.2) })
  return out
}

/** The first lift at which a person's badge touches no solid and no other badge; failing that, the highest. */
function clearLift(person, solids, badges) {
  const [ox, oy] = person.origin
  const circle = (lift) => {
    const [cx, cy] = project(CELL.w / 2, CELL.d / 2, lift)
    return { x: ox + cx - 30, y: oy + cy - 30, width: 60, height: 60 }
  }
  const touches = (rect) => solids.some((solid) => rectsOverlap(rect, solid.bounds, 0) && rectHitsPolygon(rect, solid.outline, 0))
    || badges.some((badge) => rectsOverlap(rect, boundsOfPolygon(badge), 0))
  for (const lift of LIFTS) if (!touches(circle(lift))) return lift
  return LIFTS[LIFTS.length - 1]
}

/**
 * The scene for a document.
 *   document, types (Map "id@version" to library type), states (Map node id to 'watched' | 'finding'), detail (false draws plain boxes).
 * Returns { boxes, platform, zones, solids, edges, anchors, things, bounds }. All scene space unless said (boxes and the platform are ground).
 */
export function buildScene({ document, types, states = new Map(), detail = true, boxes: given }) {
  const boxes = given || sceneBoxes(document, types)
  const empty = { boxes, platform: platformBox(document, boxes, types), zones: [], solids: [], floats: [], edges: [], anchors: new Map(), things: [], bounds: null }
  if (!document) return empty
  const platform = empty.platform
  const byId = new Map([...document.groups, ...document.nodes].map((item) => [item.id, item]))
  const depthOf = (id) => {
    let level = 0
    for (let up = parentOf(byId.get(id) || {}); up && byId.has(up) && level < 64; up = parentOf(byId.get(up))) level += 1
    return level
  }

  // A zone that holds one other zone and nothing else (a connection around its region around its account) is only a frame:
  // it draws its floor and no walls of its own, so nested frames do not stack into a row of parallel lines.
  const children = new Map()
  for (const item of [...document.groups, ...document.nodes]) {
    const up = parentOf(item)
    if (!up) continue
    if (!children.has(up)) children.set(up, { groups: 0, nodes: 0 })
    children.get(up)[document.groups.includes(item) ? 'groups' : 'nodes'] += 1
  }
  const frameOnly = (id) => {
    const inside = children.get(id)
    return Boolean(inside) && inside.groups === 1 && inside.nodes === 0
  }

  // zones: groups, parents before the groups inside them
  const zones = document.groups.filter((group) => boxes.has(group.id)).map((group) => {
    const box = boxes.get(group.id)
    const depth = depthOf(group.id)
    const corners = [[box.x, box.y], [box.x + box.width, box.y], [box.x + box.width, box.y + box.height], [box.x, box.y + box.height]]
    const wall = frameOnly(group.id) ? 0 : ZONE_WALL * (depth === 0 ? 1 : 0.75)
    const outline = hull([...corners.map(([x, y]) => groundToScene(x, y)), ...corners.slice(0, 3).map(([x, y]) => groundToScene(x, y, wall))])
    const [lx, ly] = groundToScene(box.x, box.y)
    const type = types.get(group.type)
    const state = describe(group, type)
    return {
      id: group.id, item: group, box, depth, wall, outline, bounds: boundsOfPolygon(outline), isZone: true,
      origin: groundToScene(box.x, box.y), label: { x: lx, y: ly - wall * SCALE }, typeName: type?.display_name || group.kind || group.type,
      draft: state.state === 'draft', foot: { x0: box.x / UNIT_X, x1: (box.x + box.width) / UNIT_X, y0: box.y / UNIT_Y, y1: (box.y + box.height) / UNIT_Y },
    }
  }).sort((a, b) => a.depth - b.depth || (a.box.x + a.box.y) - (b.box.x + b.box.y))

  // solids: one per node. The busiest stand tallest and the busiest of all is the deep teal tower, as in the site's picture.
  const busy = busiest(document, types, boxes)
  const solids = []
  const floats = []
  const anchors = new Map()
  const make = (node, lift) => {
    const box = boxes.get(node.id)
    const type = types.get(node.type)
    const name = recipeName(typeIdOf(node, types), type?.family)
    const spec = SPECS[name]
    const described = describe(node, type)
    const state = states.get(node.id)
    const isTower = busy.tower === node.id
    // A type the library lacks is a problem (red); one the library has not loaded yet is not, so it is drawn plain.
    const tone = !type ? (types.size ? 'c' : 'w') : isTower && state !== 'finding' ? 'd' : toneFor(spec, state)
    const isDraft = described.state === 'draft' || described.state === 'generic' || described.state === 'unknown'
    const drawn = solid(name, { tone, detail, draft: isDraft, tall: busy.tall.get(node.id) || 1, lift })
    const [ox, oy] = groundToScene(box.x, box.y)
    const outline = drawn.outline.map(([x, y]) => [x + ox, y + oy])
    const w = spec.w || (spec.r || 0.5) * 2
    const d = spec.d || (spec.r || 0.5) * 2
    const fx = box.x / UNIT_X + (CELL.w - w) / 2
    const fy = box.y / UNIT_Y + (CELL.d - d) / 2
    const at = ([x, y]) => [x + ox, y + oy]
    return {
      id: node.id, item: node, type, name, spec, tone, described, state, markup: drawn.markup, origin: [ox, oy], outline, bounds: boundsOfPolygon(outline),
      top: at(drawn.top), bottom: at(drawn.bottom), nameAt: at(drawn.nameAt || drawn.bottom), badge: drawn.badge ? drawn.badge.map(at) : null, height: drawn.height, floats: drawn.floats,
      draft: isDraft, box, lift,
      foot: { x0: fx, x1: fx + w, y0: fy, y1: fy + d },
      typeName: type?.display_name || node.type, shortName: shortName(node.name),
      degree: busy.degree.get(node.id) || 0, isTower,
    }
  }
  for (const node of document.nodes) {
    if (!boxes.has(node.id)) continue
    const item = make(node, LIFT)
    const [ox, oy] = item.origin
    anchors.set(node.id, [ox + project(CELL.w / 2, CELL.d / 2, 0)[0], oy + project(CELL.w / 2, CELL.d / 2, 0)[1]])
    ;(item.floats ? floats : solids).push(item)
  }
  // A person floats high enough that its badge clears the solids around it: the lowest lift at which it covers none of them.
  const badges = []
  floats.forEach((item, i) => {
    const wanted = clearLift(item, solids, badges)
    if (wanted !== item.lift) floats[i] = make(item.item, wanted)
    badges.push(floats[i].badge)
  })
  for (const zone of zones) {
    const [cx, cy] = groundToScene(zone.box.x + zone.box.width / 2, zone.box.y + zone.box.height / 2)
    anchors.set(zone.id, [cx, cy])
  }
  const ordered = paintOrder(solids)

  // connections: straight dashed lines between the tops of what they join
  const edges = []
  for (const edge of drawnEdges(document)) {
    const a = anchors.get(edge.from)
    const b = anchors.get(edge.to)
    if (!a || !b) continue
    edges.push({ id: edge.key, edge, from: edge.from, to: edge.to, points: [a, b] })
  }

  const things = [...zones, ...ordered, ...floats]
  const [px, py] = groundToScene(platform.x, platform.y)
  const slab = [[platform.x, platform.y], [platform.x + platform.width, platform.y], [platform.x + platform.width, platform.y + platform.height], [platform.x, platform.y + platform.height]]
    .map(([x, y]) => groundToScene(x, y))
  const all = [...slab, ...slab.map(([x, y]) => [x, y + BASE * SCALE]), ...things.flatMap((thing) => thing.outline)]
  return {
    boxes, platform, platformOrigin: [px, py], zones, solids: ordered, floats, edges, anchors, things, bounds: boundsOfPolygon(all),
  }
}

/** The words a screen reader gets for one item. */
export function ariaFor(item, type) {
  return accessibleName(item, type, describe(item, type))
}

export { NODE_H, NODE_W }
