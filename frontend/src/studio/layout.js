// Turns the places a clone is drawn at into React Flow nodes. Where the places come from (stored, or the automatic layout) is autolayout.js.
import { PLAN_SPACING, placesFor } from './autolayout.js'
import { NODE_H, NODE_W, parentOf } from './geometry.js'
import { STRUCTURAL } from './model.js'

export { NODE_H, NODE_W }

/** Every drawn edge with a stable key. Nesting already shows where a node sits, so contained_in is not drawn twice. */
export function visibleEdges(document) {
  const location = new Map((document.nodes || []).map((n) => [n.id, n.location]))
  return (document.edges || [])
    .filter((e) => !(e.type === STRUCTURAL && location.get(e.from) === e.to))
    .map((e, i) => ({ ...e, key: `e${i}-${e.from}-${e.type}-${e.to}` }))
}

/** React Flow wants parents before children and a child's position relative to its parent. `types` is the library (Map "id@version" to type). */
export function planFlow(document, types) {
  const boxes = placesFor(document, types, PLAN_SPACING)
  const items = new Map([...(document.groups || []), ...(document.nodes || [])].map((item) => [item.id, item]))
  const groups = new Set((document.groups || []).map((g) => g.id))
  const depth = (id) => {
    let level = 0
    for (let up = parentOf(items.get(id)); up && items.has(up) && level < 64; up = parentOf(items.get(up))) level += 1
    return level
  }
  const ordered = [...items.values()].filter((item) => boxes.has(item.id)).sort((a, b) => depth(a.id) - depth(b.id))
  return ordered.map((item) => {
    const box = boxes.get(item.id)
    const parent = parentOf(item)
    const origin = parent && items.has(parent) ? boxes.get(parent) : { x: 0, y: 0 }
    const base = { id: item.id, position: { x: box.x - origin.x, y: box.y - origin.y }, ...(parent && items.has(parent) ? { parentId: parent } : {}), data: { item }, draggable: false }
    if (!groups.has(item.id)) return { ...base, type: 'estate', width: NODE_W, height: NODE_H }
    return { ...base, type: 'boundary', selectable: true, width: box.width, height: box.height }
  })
}
