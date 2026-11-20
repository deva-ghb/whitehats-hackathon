// Where things sit on the canvas, and the edits that move, resize, align and group them.
// No I/O and no React, so every rule here can be tested with plain objects.
//
// An item's `layout` is relative to the group it sits in, so moving a group moves everything inside it.
// Every function that changes a position needs a placed document (every item has a layout):
// `freezeLayout` turns what the auto layout drew into stored positions the first time a person edits.

import { isConnectionType } from './connection.js'

export const NODE_W = 232
export const NODE_H = 72
export const GRID = 8
// Room a group keeps around what it holds: the title strip on top, a little air elsewhere.
export const GROUP_PAD = { top: 44, side: 20, bottom: 20 }
export const GROUP_MIN = { width: 160, height: 112 }
export const NEW_GROUP = { width: 392, height: 256 }
const GAP = 24

export const snap = (value) => Math.round(value / GRID) * GRID
const ceilToGrid = (value) => Math.ceil(value / GRID) * GRID
export const parentOf = (item) => item.location || item.parent || null

const everyItem = (document) => [...(document.groups || []), ...(document.nodes || [])]
const groupIdsOf = (document) => new Set((document.groups || []).map((group) => group.id))
const sizeOf = (item, isGroup) => (isGroup
  ? { width: item.layout?.width ?? NEW_GROUP.width, height: item.layout?.height ?? NEW_GROUP.height }
  : { width: NODE_W, height: NODE_H })

/** True when at least one item has a stored position. */
export function hasStoredLayout(document) {
  return everyItem(document).some((item) => item.layout)
}

/** True when every item has a stored position (an empty clone counts: there is nothing to draw). */
export function isPlaced(document) {
  return everyItem(document).every((item) => item.layout)
}

/** Absolute boxes (x, y, width, height) of every placed item, walking up through its groups. */
export function absoluteBoxes(document) {
  const items = new Map(everyItem(document).map((item) => [item.id, item]))
  const groups = groupIdsOf(document)
  const boxes = new Map()
  const resolve = (id, trail) => {
    if (boxes.has(id)) return boxes.get(id)
    const item = items.get(id)
    if (!item?.layout || trail.has(id)) return null
    const parent = parentOf(item)
    const origin = parent && items.has(parent) ? resolve(parent, new Set([...trail, id])) : { x: 0, y: 0 }
    if (!origin) return null
    const box = { x: origin.x + item.layout.x, y: origin.y + item.layout.y, ...sizeOf(item, groups.has(id)) }
    boxes.set(id, box)
    return box
  }
  for (const id of items.keys()) resolve(id, new Set())
  return boxes
}

const whole = (value) => Math.round(value)
const sameLayout = (a, b) => Boolean(a) && ['x', 'y', 'width', 'height'].every((key) => a[key] === b[key])

function writeLayouts(document, boxes) {
  const groups = groupIdsOf(document)
  const write = (item) => {
    const box = boxes.get(item.id)
    if (!box) return item
    const origin = boxes.get(parentOf(item)) || { x: 0, y: 0 }
    const layout = { x: whole(box.x - origin.x), y: whole(box.y - origin.y) }
    if (groups.has(item.id)) Object.assign(layout, { width: whole(box.width), height: whole(box.height) })
    return { ...item, layout }
  }
  return { ...document, nodes: document.nodes.map(write), groups: document.groups.map(write) }
}

/**
 * Keep what is on screen: store the position of every item from the boxes the canvas drew.
 * A clone that is already placed comes back unchanged.
 */
export function freezeLayout(document, drawn) {
  if (isPlaced(document)) return document
  return writeLayouts(document, drawn)
}

/** Forget every stored position, so the next draw lays the clone out again. */
export function clearLayout(document) {
  const strip = ({ layout, ...rest }) => rest
  return { ...document, nodes: document.nodes.map(strip), groups: document.groups.map(strip) }
}

/** Every id inside this group, at any depth. */
export function descendants(document, id) {
  const found = new Set()
  const walk = (parent) => {
    for (const item of everyItem(document)) {
      if (parentOf(item) === parent && !found.has(item.id)) {
        found.add(item.id)
        walk(item.id)
      }
    }
  }
  walk(id)
  return found
}

/** Of these ids, the ones that are not inside another of them: moving those moves the rest. */
export function topOnes(document, ids) {
  const chosen = new Set(ids)
  const items = new Map(everyItem(document).map((item) => [item.id, item]))
  const insideChosen = (id) => {
    let up = parentOf(items.get(id) || {})
    const seen = new Set()
    while (up && !seen.has(up)) {
      if (chosen.has(up)) return true
      seen.add(up)
      up = parentOf(items.get(up) || {})
    }
    return false
  }
  return [...chosen].filter((id) => items.has(id) && !insideChosen(id))
}

const area = (box) => box.width * box.height
const holds = (box, point) => point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height
const centreOf = (box) => ({ x: box.x + box.width / 2, y: box.y + box.height / 2 })

/** The smallest group (not in `banned`) whose box holds the point, or null. */
export function containerAt(document, boxes, point, banned = new Set()) {
  let best = null
  for (const group of document.groups) {
    const box = boxes.get(group.id)
    if (banned.has(group.id) || !box || !holds(box, point)) continue
    if (!best || area(box) < area(best.box)) best = { id: group.id, box }
  }
  return best?.id ?? null
}

function setParent(item, isGroup, parentId) {
  const key = isGroup ? 'parent' : 'location'
  const { [key]: _old, ...rest } = item
  return parentId ? { ...rest, [key]: parentId } : rest
}

const depthOf = (items, id) => {
  let depth = 0
  let up = parentOf(items.get(id) || {})
  while (up && depth < 64) {
    depth += 1
    up = parentOf(items.get(up) || {})
  }
  return depth
}

/** Make every group big enough for what is inside it, deepest first, so a group always contains its children. */
export function growGroups(document) {
  const items = new Map(everyItem(document).map((item) => [item.id, item]))
  const groupIds = groupIdsOf(document)
  const groups = [...document.groups].sort((a, b) => depthOf(items, b.id) - depthOf(items, a.id))
  const sized = new Map()
  for (const group of groups) {
    const kids = everyItem(document).filter((item) => parentOf(item) === group.id && item.layout)
    let { width, height } = sizeOf(group, true)
    for (const kid of kids) {
      const size = groupIds.has(kid.id) ? sized.get(kid.id) : sizeOf(kid, false)
      width = Math.max(width, ceilToGrid(kid.layout.x + size.width + GROUP_PAD.side))
      height = Math.max(height, ceilToGrid(kid.layout.y + size.height + GROUP_PAD.bottom))
    }
    sized.set(group.id, { width, height })
  }
  const grow = (group) => {
    const size = sized.get(group.id)
    if (!group.layout || (size.width === group.layout.width && size.height === group.layout.height)) return group
    return { ...group, layout: { ...group.layout, width: size.width, height: size.height } }
  }
  const grown = document.groups.map(grow)
  return grown.every((group, index) => group === document.groups[index]) ? document : { ...document, groups: grown }
}

/**
 * Move items to new top left corners (absolute). A group takes everything inside it along.
 * With `reparent`, an item whose middle lands in a group now sits in that group, and one dropped on the bare canvas leaves its group.
 * With `onGrid`, the place it settles at (counted from its group) is a multiple of the grid.
 */
export function moveItems(document, moves, { reparent = true, onGrid = false } = {}) {
  const settle = onGrid ? snap : whole
  const boxes = absoluteBoxes(document)
  const groups = groupIdsOf(document)
  const roots = topOnes(document, [...moves.keys()]).filter((id) => boxes.has(id))
  const working = new Map(boxes)
  for (const id of roots) working.set(id, { ...boxes.get(id), ...moves.get(id) })
  const changes = new Map()
  for (const id of roots) {
    const box = working.get(id)
    const item = everyItem(document).find((entry) => entry.id === id)
    const banned = new Set([id, ...descendants(document, id)])
    // A connection is a root of the picture: it is never put inside another container, wherever it is dropped.
    const parentId = reparent && !isConnectionType(item.type) ? containerAt(document, working, centreOf(box), banned) : parentOf(item)
    const origin = (parentId && working.get(parentId)) || { x: 0, y: 0 }
    let x = box.x - origin.x
    let y = box.y - origin.y
    if (parentId) {
      x = Math.max(GROUP_PAD.side, x)
      y = Math.max(GROUP_PAD.top, y)
    }
    changes.set(id, { parentId, layout: { x: settle(x), y: settle(y), ...(groups.has(id) ? { width: whole(box.width), height: whole(box.height) } : {}) } })
  }
  const apply = (item) => {
    const change = changes.get(item.id)
    if (!change || (parentOf(item) === change.parentId && sameLayout(item.layout, change.layout))) return item
    return { ...setParent(item, groups.has(item.id), change.parentId), layout: change.layout }
  }
  const nodes = document.nodes.map(apply)
  const moved = document.groups.map(apply)
  const unchanged = [...nodes, ...moved].every((item, index) => item === [...document.nodes, ...document.groups][index])
  return unchanged ? document : growGroups({ ...document, nodes, groups: moved })
}

/** Move by a distance (in pixels), staying in the same group. */
export function nudgeItems(document, ids, dx, dy) {
  const boxes = absoluteBoxes(document)
  const moves = new Map()
  for (const id of topOnes(document, ids)) {
    const box = boxes.get(id)
    if (box) moves.set(id, { x: box.x + dx, y: box.y + dy })
  }
  return moves.size ? moveItems(document, moves, { reparent: false }) : document
}

/**
 * Resize a group to a new absolute box. It never shrinks past what it holds, and what it holds stays where it is on screen.
 */
export function resizeGroup(document, id, wanted) {
  const boxes = absoluteBoxes(document)
  const old = boxes.get(id)
  if (!old) return document
  const kids = everyItem(document).filter((item) => parentOf(item) === id && boxes.has(item.id))
  let left = wanted.x
  let top = wanted.y
  let right = wanted.x + wanted.width
  let bottom = wanted.y + wanted.height
  if (kids.length) {
    const held = kids.map((kid) => boxes.get(kid.id))
    left = Math.min(left, Math.min(...held.map((box) => box.x)) - GROUP_PAD.side)
    top = Math.min(top, Math.min(...held.map((box) => box.y)) - GROUP_PAD.top)
    right = Math.max(right, Math.max(...held.map((box) => box.x + box.width)) + GROUP_PAD.side)
    bottom = Math.max(bottom, Math.max(...held.map((box) => box.y + box.height)) + GROUP_PAD.bottom)
  }
  right = Math.max(right, left + GROUP_MIN.width)
  bottom = Math.max(bottom, top + GROUP_MIN.height)
  const parent = boxes.get(parentOf(everyItem(document).find((item) => item.id === id))) || { x: 0, y: 0 }
  const kidIds = new Set(kids.map((kid) => kid.id))
  const place = (item) => {
    if (item.id === id) {
      return { ...item, layout: { x: whole(left - parent.x), y: whole(top - parent.y), width: whole(right - left), height: whole(bottom - top) } }
    }
    if (!kidIds.has(item.id)) return item
    const box = boxes.get(item.id)
    return { ...item, layout: { ...item.layout, x: whole(box.x - left), y: whole(box.y - top) } }
  }
  return growGroups({ ...document, nodes: document.nodes.map(place), groups: document.groups.map(place) })
}

export const ALIGNMENTS = ['left', 'center', 'right', 'top', 'middle', 'bottom']

/** Line the chosen items up on one edge or on their common middle. */
export function alignItems(document, ids, mode) {
  const boxes = absoluteBoxes(document)
  const chosen = topOnes(document, ids).filter((id) => boxes.has(id))
  if (chosen.length < 2) return document
  const all = chosen.map((id) => boxes.get(id))
  const edge = {
    left: Math.min(...all.map((box) => box.x)),
    right: Math.max(...all.map((box) => box.x + box.width)),
    top: Math.min(...all.map((box) => box.y)),
    bottom: Math.max(...all.map((box) => box.y + box.height)),
  }
  const moves = new Map()
  for (const id of chosen) {
    const box = boxes.get(id)
    const to = { x: box.x, y: box.y }
    if (mode === 'left') to.x = edge.left
    if (mode === 'right') to.x = edge.right - box.width
    if (mode === 'center') to.x = (edge.left + edge.right) / 2 - box.width / 2
    if (mode === 'top') to.y = edge.top
    if (mode === 'bottom') to.y = edge.bottom - box.height
    if (mode === 'middle') to.y = (edge.top + edge.bottom) / 2 - box.height / 2
    moves.set(id, { x: whole(to.x), y: whole(to.y) })
  }
  return moveItems(document, moves, { reparent: false })
}

/** Spread three or more items so the gaps between them are equal. */
export function distributeItems(document, ids, axis) {
  const boxes = absoluteBoxes(document)
  const chosen = topOnes(document, ids).filter((id) => boxes.has(id))
  if (chosen.length < 3) return document
  const [start, size] = axis === 'horizontal' ? ['x', 'width'] : ['y', 'height']
  const ordered = [...chosen].sort((a, b) => boxes.get(a)[start] - boxes.get(b)[start])
  const first = boxes.get(ordered[0])
  const last = boxes.get(ordered.at(-1))
  const span = last[start] + last[size] - first[start]
  const used = ordered.reduce((sum, id) => sum + boxes.get(id)[size], 0)
  const gap = (span - used) / (ordered.length - 1)
  const moves = new Map()
  let cursor = first[start]
  for (const id of ordered) {
    const box = boxes.get(id)
    moves.set(id, axis === 'horizontal' ? { x: whole(cursor), y: box.y } : { x: box.x, y: whole(cursor) })
    cursor += box[size] + gap
  }
  return moveItems(document, moves, { reparent: false })
}

const overlaps = (a, b, margin) => a.x < b.x + b.width + margin && b.x < a.x + a.width + margin
  && a.y < b.y + b.height + margin && b.y < a.y + a.height + margin

/**
 * A free place for something of this size: inside the group when one is named (first gap, row by row),
 * otherwise as near the wanted middle point as it can be without covering a neighbour. Absolute top left.
 * A point over a group counts as inside that group, so the group itself is never the neighbour to avoid.
 * With `avoid` false the spot is exactly the point (a drop with the pointer: the person chose the place).
 */
export function freeSpot(document, { size, parentId = null, near = { x: 0, y: 0 }, avoid = true, root = false }) {
  const boxes = absoluteBoxes(document)
  const parentBox = parentId ? boxes.get(parentId) : null
  if (!parentBox) {
    // Next to whatever else sits in the group under the point (or on the bare canvas), never on top of it.
    // A root (a connection) never goes into the container under the point, so it avoids what sits at the top, containers included.
    const holder = root ? null : containerAt(document, boxes, near)
    const siblings = everyItem(document).filter((item) => parentOf(item) === holder && boxes.has(item.id)).map((item) => boxes.get(item.id))
    let spot = { x: snap(near.x - size.width / 2), y: snap(near.y - size.height / 2) }
    if (!avoid) return spot
    for (let tries = 0; tries < 60; tries += 1) {
      if (!siblings.some((box) => overlaps({ ...spot, ...size }, box, GRID))) return spot
      spot = { x: spot.x + GRID * 3, y: spot.y + GRID * 3 }
    }
    return spot
  }
  const siblings = everyItem(document).filter((item) => parentOf(item) === parentId && boxes.has(item.id)).map((item) => boxes.get(item.id))
  const left = parentBox.x + GROUP_PAD.side
  const stepX = size.width + GAP
  const columns = Math.max(1, Math.floor((parentBox.width - GROUP_PAD.side * 2 + GAP) / stepX))
  for (let row = 0; row < 200; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const spot = { x: left + column * stepX, y: parentBox.y + GROUP_PAD.top + row * (size.height + GAP) }
      if (!siblings.some((box) => overlaps({ ...spot, ...size }, box, 0))) return spot
    }
  }
  return { x: left, y: parentBox.y + GROUP_PAD.top }
}

/** Put an item in another group (or on the bare canvas) without moving the rest of the clone. Refuses a group inside itself. */
export function reparentItem(document, id, parentId) {
  const item = everyItem(document).find((entry) => entry.id === id)
  const boxes = absoluteBoxes(document)
  if (!item || !boxes.has(id)) return { document }
  const isGroup = groupIdsOf(document).has(id)
  if (isGroup && parentId && (parentId === id || descendants(document, id).has(parentId))) {
    return { refusal: 'A container cannot go inside itself or inside something it holds.' }
  }
  const own = boxes.get(id)
  const wanted = parentId
    ? freeSpot(withoutItem(document, id), { size: own, parentId })
    : { x: own.x, y: own.y }
  const origin = (parentId && boxes.get(parentId)) || { x: 0, y: 0 }
  const layout = { ...item.layout, x: whole(wanted.x - origin.x), y: whole(wanted.y - origin.y) }
  const write = (entry) => (entry.id === id ? { ...setParent(entry, isGroup, parentId), layout } : entry)
  return { document: growGroups({ ...document, nodes: document.nodes.map(write), groups: document.groups.map(write) }) }
}

function withoutItem(document, id) {
  return { ...document, nodes: document.nodes.filter((n) => n.id !== id), groups: document.groups.filter((g) => g.id !== id) }
}

/** The box that wraps these items, with the room a new group keeps, snapped outward to the grid. Absolute. */
export function boxAround(boxes, ids) {
  const held = ids.map((id) => boxes.get(id)).filter(Boolean)
  const left = Math.min(...held.map((box) => box.x)) - GROUP_PAD.side
  const top = Math.min(...held.map((box) => box.y)) - GROUP_PAD.top
  const right = Math.max(...held.map((box) => box.x + box.width)) + GROUP_PAD.side
  const bottom = Math.max(...held.map((box) => box.y + box.height)) + GROUP_PAD.bottom
  const x = Math.floor(left / GRID) * GRID
  const y = Math.floor(top / GRID) * GRID
  return { x, y, width: Math.ceil((right - x) / GRID) * GRID, height: Math.ceil((bottom - y) / GRID) * GRID }
}

/** The boxes the canvas drew, from its nodes (positions are relative to the parent node). Absolute. */
export function boxesFromFlow(flowNodes) {
  const byId = new Map(flowNodes.map((node) => [node.id, node]))
  const boxes = new Map()
  const resolve = (node) => {
    if (boxes.has(node.id)) return boxes.get(node.id)
    const parent = node.parentId && byId.get(node.parentId) ? resolve(byId.get(node.parentId)) : { x: 0, y: 0 }
    const isGroup = node.type === 'boundary'
    const box = {
      x: parent.x + node.position.x, y: parent.y + node.position.y,
      width: isGroup ? Number(node.width) || NEW_GROUP.width : NODE_W,
      height: isGroup ? Number(node.height) || NEW_GROUP.height : NODE_H,
    }
    boxes.set(node.id, box)
    return box
  }
  for (const node of flowNodes) resolve(node)
  return boxes
}
