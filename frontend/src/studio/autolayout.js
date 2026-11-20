// The automatic layout for a clone that has no stored places: one picture both views draw. Pure, no drawing, no I/O.
//
// It follows the clone's own structure. A container holds its children in bands, top to bottom: people and roles first, then the
// public edge, delivery, compute, queues, and last the data stores, so what comes in sits above and what keeps the data sits low.
// Each band wraps into columns, the number chosen so the container is close to the wanted shape, and inside a band the nodes follow
// the connections of the band above them. Containers are packed beside each other (packing.js). What belongs outside the estate
// (the public edge and visitors, then partners and repositories) stands in a compact block at each side of it.
// The same clone always gets the same places.
import {
  GRID, GROUP_MIN, GROUP_PAD, NEW_GROUP, NODE_H, NODE_W, absoluteBoxes, freeSpot, growGroups, hasStoredLayout, isPlaced, parentOf,
} from './geometry.js'
import { isConnectionType } from './connection.js'
import { STRUCTURAL } from './model.js'
import { packRects } from './packing.js'
import { SPECS, recipeName } from './scene/recipes.js'

const NODE_BLOCK = '#nodes'
// A connection's label (its name, "Connection" and where it stands) needs more room than a plain container's.
const CONNECTION_MIN = { width: 360, height: 160 }
const BANDS = ['identity', 'entry', 'delivery', 'compute', 'queue', 'data', 'other']
const BAND_OF = {
  person: 'identity', edge: 'entry', visitor: 'entry', repo: 'delivery', pipeline: 'delivery', saas: 'delivery', laptop: 'delivery',
  queue: 'queue', data: 'data', object: 'data', cache: 'data', vault: 'data', logs: 'other', control: 'other', note: 'other',
}
export const EMPTY_ESTATE = { x: 0, y: 0, width: 960, height: 576 }   // the ground an estate with nothing inside it still has
const MOST_COLUMNS = 16
const BLOCK_SHAPES = [0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.6, 1.8, 2, 2.3, 2.6, 3, 3.5]   // the shapes tried for the blocks of nodes and containers (width over height)
const OFF_SHAPE = 1.2                    // how much a block is penalised for being off the wanted shape, as a share of its area per unit of ln(ratio)

// The room the plan has on screen at the two widths people use most: the canvas between the library and the inspector, less the margin a
// fitted view keeps (1440 by 900, and 1920 by 1080). A layout is judged by how large it can be drawn there. Names read from half size,
// so beyond that the large screen gains nothing, while the small one keeps gaining: it is the one that cannot reach it for a busy map.
const SMALL_PANE = { width: 564, height: 558 }
const LARGE_PANE = { width: 1044, height: 788 }
const READS_FROM = 0.55
const zoomIn = (pane, width, height) => Math.min(pane.width / width, pane.height / height)
const planCost = (width, height) => 1 / (Math.min(zoomIn(LARGE_PANE, width, height), READS_FROM) * Math.sqrt(zoomIn(SMALL_PANE, width, height)))

/**
 * What a view wants of the layout. `gap` is the space between cards; `outside` the distance of what stands outside the estate;
 * `outsideAt` is 'top' (a row above the estate) or 'corners' (a block at each corner); `cost(width, height)` is lower the better a
 * picture of that size reads. The plan packs tightly; the scene (scene/placement.js) leaves room for solids taller than a card.
 */
export const PLAN_SPACING = { gap: { x: 24, y: 24 }, outside: { x: 0, y: 40 }, outsideAt: 'top', cost: planCost }

const ceilToGrid = (value) => Math.ceil(value / GRID) * GRID
const everyItem = (document) => [...(document.groups || []), ...(document.nodes || [])]
const recipeOf = (item, types) => {
  const type = types.get(item.type)
  return recipeName(type?.id, type?.family)
}

/** Which side a kind of node stands on when nothing says otherwise: 'left' (comes in from outside), 'right' (the estate reaches it), or null (inside). */
export function outsideSide(item, types) {
  return SPECS[recipeOf(item, types)]?.outside || null
}

/** The band a node sits in inside its container: its place in the order above. */
export function bandOf(item, types) {
  const name = BAND_OF[recipeOf(item, types)] || 'compute'
  return BANDS.indexOf(name)
}

/** Who each node is connected to, in either direction. Nesting is not a connection. */
function neighboursOf(document) {
  const near = new Map()
  const note = (a, b) => near.set(a, [...(near.get(a) || []), b])
  for (const edge of document.edges || []) {
    if (edge.type === STRUCTURAL) continue
    note(edge.from, edge.to)
    note(edge.to, edge.from)
  }
  return near
}

const byName = (a, b) => a.name.localeCompare(b.name) || String(a.id).localeCompare(String(b.id))

/** One band as a list of nodes, those whose neighbours stand further left first, so a line goes down rather than across. */
function bandOrder(band, near, column) {
  const pull = (node) => {
    const spots = (near.get(node.id) || []).filter((id) => column.has(id)).map((id) => column.get(id))
    return spots.length ? spots.reduce((sum, spot) => sum + spot, 0) / spots.length : Infinity
  }
  return [...band].sort((a, b) => pull(a) - pull(b) || byName(a, b))
}

/**
 * The nodes of one container, band after band, filling rows left to right, `columns` wide at most. The bands run top to bottom
 * in their order, so what comes in sits above and the data stores low. Returns { at, width, height }.
 */
function bandedBlock(nodes, columns, { gap }, types, near) {
  const bands = BANDS.map((_, index) => nodes.filter((node) => bandOf(node, types) === index)).filter((list) => list.length)
  const column = new Map()
  const at = new Map()
  let place = 0
  for (const list of bands) {
    for (const node of bandOrder(list, near, column)) {
      const c = place % columns
      at.set(node.id, { x: c * (NODE_W + gap.x), y: Math.floor(place / columns) * (NODE_H + gap.y) })
      column.set(node.id, c)
      place += 1
    }
  }
  const used = Math.min(columns, nodes.length)
  const rows = Math.ceil(nodes.length / columns)
  return { at, width: used * NODE_W + (used - 1) * gap.x, height: rows * NODE_H + (rows - 1) * gap.y }
}

/** The number of columns whose block `cost` likes best. */
function bestColumns(count, make, cost) {
  let best = null
  for (let columns = 1; columns <= Math.min(count, MOST_COLUMNS); columns += 1) {
    const made = make(columns)
    const price = cost(made)
    if (!best || price < best.price - 1e-6) best = { price, made }
  }
  return best.made
}

/** Area, dearer the further a block is from the shape wanted (width over height). */
const closeTo = (shape) => (block) => block.width * block.height * (1 + OFF_SHAPE * Math.abs(Math.log(block.width / block.height / shape)))

/** Plain rows and columns, for what stands outside the estate. With `columns` given it is that wide at most; otherwise as square as it goes. */
function gridBlock(nodes, spacing, columns = null) {
  const { gap } = spacing
  const make = (across) => {
    const at = new Map()
    nodes.forEach((node, i) => at.set(node.id, { x: (i % across) * (NODE_W + gap.x), y: Math.floor(i / across) * (NODE_H + gap.y) }))
    const used = Math.min(across, nodes.length)
    const rows = Math.ceil(nodes.length / across)
    return { at, width: used * NODE_W + (used - 1) * gap.x, height: rows * NODE_H + (rows - 1) * gap.y }
  }
  if (!nodes.length) return { at: new Map(), width: 0, height: 0 }
  return columns ? make(Math.min(columns, nodes.length)) : bestColumns(nodes.length, make, (block) => spacing.cost(block.width, block.height))
}

/** Who is inside what, and who is connected to whom: worked out once for every try. */
function structureOf(document) {
  const kidsOf = new Map()
  for (const item of everyItem(document)) {
    const parent = parentOf(item) || null
    if (!kidsOf.has(parent)) kidsOf.set(parent, [])
    kidsOf.get(parent).push(item)
  }
  // `blocks` keeps each container's block of nodes for each number of columns: it does not depend on the shape tried, so it is built once.
  const connections = new Set((document.groups || []).filter((group) => isConnectionType(group.type)).map((group) => group.id))
  return { kidsOf, isGroup: new Set((document.groups || []).map((group) => group.id)), connections, near: neighboursOf(document), blocks: new Map() }
}

/**
 * One layout of the whole clone, with the nodes of every container in blocks of the shape `shape` (width over height) and the
 * containers packed to the same shape. A container is sized by what it holds, so it is laid out before the one around it.
 */
function layOnce({ kidsOf, isGroup, connections, near, blocks }, types, spacing, shape) {
  const { gap } = spacing
  const sizes = new Map()
  const fills = new Map()

  // What a container holds, counted from its content corner: its nodes as one block, packed with its containers so that a
  // small container tucks in beside a big one.
  const fill = (parentId) => {
    const kids = (kidsOf.get(parentId) || []).filter((kid) => parentId !== null || isGroup.has(kid.id) || !outsideSide(kid, types))
    const groups = kids.filter((kid) => isGroup.has(kid.id))
    const nodes = kids.filter((kid) => !isGroup.has(kid.id))
    const blockOf = (columns) => {
      const key = `${parentId}|${columns}`
      if (!blocks.has(key)) blocks.set(key, bandedBlock(nodes, columns, spacing, types, near))
      return blocks.get(key)
    }
    const block = nodes.length ? bestColumns(nodes.length, blockOf, closeTo(shape)) : null
    const cells = groups.map((kid) => ({ id: kid.id, ...sizeOfGroup(kid.id) }))
    if (block) cells.push({ id: NODE_BLOCK, width: block.width, height: block.height })
    cells.sort((a, b) => b.width * b.height - a.width * a.height || String(a.id).localeCompare(String(b.id)))
    const packed = packRects(cells, { gap, aspect: shape })
    const at = new Map(groups.map((kid) => [kid.id, packed.at.get(kid.id)]))
    for (const kid of nodes) {
      const corner = packed.at.get(NODE_BLOCK)
      at.set(kid.id, { x: corner.x + block.at.get(kid.id).x, y: corner.y + block.at.get(kid.id).y })
    }
    return { kids: [...groups, ...nodes], at, width: packed.width, height: packed.height }
  }
  const sizeOfGroup = (id) => {
    if (!sizes.has(id)) {
      const inner = fill(id)
      fills.set(id, inner)
      sizes.set(id, {
        width: Math.max(connections.has(id) ? CONNECTION_MIN.width : GROUP_MIN.width, ceilToGrid(inner.width + GROUP_PAD.side * 2)),
        height: Math.max(connections.has(id) ? CONNECTION_MIN.height : GROUP_MIN.height, ceilToGrid(inner.height + GROUP_PAD.top + GROUP_PAD.bottom)),
      })
    }
    return sizes.get(id)
  }

  const boxes = new Map()
  const place = (origin, inner) => {
    for (const kid of inner.kids) {
      const spot = inner.at.get(kid.id)
      const box = { x: origin.x + spot.x, y: origin.y + spot.y }
      if (!isGroup.has(kid.id)) {
        boxes.set(kid.id, { ...box, width: NODE_W, height: NODE_H })
        continue
      }
      boxes.set(kid.id, { ...box, ...sizeOfGroup(kid.id) })
      place({ x: box.x + GROUP_PAD.side, y: box.y + GROUP_PAD.top }, fills.get(kid.id))
    }
  }
  const top = fill(null)
  place({ x: 0, y: 0 }, top)
  const estate = top.kids.length ? { x: 0, y: 0, width: top.width, height: top.height } : EMPTY_ESTATE
  standOutside(types, spacing, estate, { kidsOf, isGroup }, boxes)
  return boxes
}

/** The size of the whole picture, judged by the view's own cost. */
function costOf(boxes, cost) {
  const all = [...boxes.values()]
  if (!all.length) return 0
  const width = Math.max(...all.map((box) => box.x + box.width)) - Math.min(...all.map((box) => box.x))
  const height = Math.max(...all.map((box) => box.y + box.height)) - Math.min(...all.map((box) => box.y))
  return cost(width, height)
}

/**
 * The absolute box {x, y, width, height} of every item of a clone, as if none had a stored place. `types` is the library
 * (Map "id@version" to type); `spacing` is PLAN_SPACING or the scene's. Several block shapes are tried, because the shape of the
 * whole depends on how deep the containers nest; the one that the view's cost likes best is kept.
 */
export function autoBoxes(document, types, spacing = PLAN_SPACING) {
  const structure = structureOf(document)
  let best = null
  for (const shape of BLOCK_SHAPES) {
    const boxes = layOnce(structure, types, spacing, shape)
    const cost = costOf(boxes, spacing.cost)
    if (!best || cost < best.cost - 1e-6) best = { cost, boxes }
  }
  return best.boxes
}

/**
 * What stands outside the estate: the public edge and visitors come in from the left, partners and repositories sit at the right.
 * The scene keeps a compact block at each corner of its platform. The plan lays both in a row above the estate, so the picture
 * stays as wide as it is tall.
 */
function standOutside(types, spacing, estate, { kidsOf, isGroup }, boxes) {
  const outside = (kidsOf.get(null) || []).filter((kid) => !isGroup.has(kid.id) && outsideSide(kid, types)).sort(byName)
  const lists = { left: outside.filter((kid) => outsideSide(kid, types) === 'left'), right: outside.filter((kid) => outsideSide(kid, types) === 'right') }
  const put = (list, block, x, y) => {
    for (const kid of list) {
      const spot = block.at.get(kid.id)
      boxes.set(kid.id, { x: x + spot.x, y: y + spot.y, width: NODE_W, height: NODE_H })
    }
  }
  if (spacing.outsideAt === 'top') {
    const both = lists.left.length && lists.right.length
    const across = Math.max(1, Math.floor(((both ? estate.width / 2 : estate.width) + spacing.gap.x) / (NODE_W + spacing.gap.x)))
    const blocks = { left: gridBlock(lists.left, spacing, across), right: gridBlock(lists.right, spacing, across) }
    const y = -Math.max(blocks.left.height, blocks.right.height) - spacing.outside.y
    put(lists.left, blocks.left, estate.x, y)
    put(lists.right, blocks.right, estate.x + estate.width - blocks.right.width, y)
    return
  }
  const left = gridBlock(lists.left, spacing)
  const right = gridBlock(lists.right, spacing)
  put(lists.left, left, estate.x - spacing.outside.x - left.width, estate.y + estate.height + spacing.outside.y - left.height)
  put(lists.right, right, estate.x + estate.width + spacing.outside.x, estate.y - spacing.outside.y)
}

/** Give places to the few items of a placed clone that have none (an import adds items): next to what is there, never on it. */
function placeTheRest(document, { gap, outside }) {
  let working = document
  const placed = absoluteBoxes(document)
  const right = placed.size ? Math.max(...[...placed.values()].map((box) => box.x + box.width)) : 0
  const top = placed.size ? Math.min(...[...placed.values()].map((box) => box.y)) : 0
  const isGroup = new Set((document.groups || []).map((group) => group.id))
  const unplaced = everyItem(document).filter((item) => !item.layout)
  let row = 0
  for (const item of unplaced) {
    const parent = parentOf(item)
    const size = isGroup.has(item.id) ? NEW_GROUP : { width: NODE_W, height: NODE_H }
    const parentBox = absoluteBoxes(working).get(parent)
    const near = parentBox ? undefined : { x: right + outside.x + size.width / 2, y: top + row * (size.height + gap.y) + size.height / 2 }
    const spot = freeSpot(working, { size, parentId: parentBox ? parent : null, near })
    const origin = parentBox || { x: 0, y: 0 }
    const layout = { x: spot.x - origin.x, y: spot.y - origin.y, ...(isGroup.has(item.id) ? size : {}) }
    const write = (entry) => (entry.id === item.id ? { ...entry, layout } : entry)
    working = { ...working, nodes: working.nodes.map(write), groups: working.groups.map(write) }
    if (!parentBox) row += 1
  }
  return absoluteBoxes(growGroups(working))
}

/** The absolute box of every item as a view draws it: the stored places, with the automatic ones filling any gap or the whole picture. */
export function placesFor(document, types, spacing = PLAN_SPACING) {
  if (isPlaced(document)) return absoluteBoxes(document)
  if (hasStoredLayout(document)) return placeTheRest(document, spacing)
  return autoBoxes(document, types, spacing)
}
