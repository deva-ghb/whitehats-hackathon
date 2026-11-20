// Pure edits of a clone document: place a type, change a property, draw or refuse an edge.
// No I/O. The canvas saves the document this returns.

import {
  NEW_GROUP, NODE_H, NODE_W, absoluteBoxes, boxAround, freeSpot, growGroups, moveItems, parentOf, topOnes,
} from './geometry.js'
import { humanKey } from './model.js'
import { connectionKind, connectorKey, isConnectionType } from './connection.js'

// Same names as the clone schema's edge enum, plus the escape hatch at the end (design B4a).
export const EDGE_TYPES = [
  'calls', 'publishes_to', 'consumes_from', 'reads', 'writes', 'deploys_from', 'built_by',
  'pushes_image_to', 'runs_on', 'runs_as', 'contained_in', 'routes_through', 'authenticates_via',
  'stores_secret_in', 'emits_telemetry_to', 'replicates_to', 'backs_up_to', 'depends_on',
  'peers_with', 'syncs_with', 'generic_relation',
]

export const GENERIC_RELATION = 'generic_relation'
// What a thing drawn by hand says about where it came from; an import says its own source instead.
const DRAWN = { kind: 'drawn', source: 'Clone Studio' }

const ENVIRONMENTS = [
  ['prod', 'Production'],
  ['staging', 'Staging'],
  ['dev', 'Development'],
  ['dr', 'Disaster recovery'],
  ['shared', 'Shared'],
]

export const ENVIRONMENT_OPTIONS = ENVIRONMENTS.map(([value, label]) => ({ value, label }))

// A boundary type becomes a group. The kind is the schema's word for that boundary.
const BOUNDARY_KIND = {
  'boundary.aws-organization': 'organisation',
  'boundary.aws-account': 'account',
  'boundary.subscription': 'subscription',
  'boundary.resource-group': 'resource-group',
  'boundary.aws-region': 'region',
  'boundary.vpc': 'vpc',
  'boundary.subnet': 'subnet',
  'boundary.security-group': 'security-group',
  'boundary.eks': 'cluster',
  'boundary.aks': 'cluster',
  'boundary.entra-tenant': 'account',
  'boundary.corporate-network': 'trust-zone',
  'generic.boundaries': 'trust-zone',
}

export function blankClone({ slug, environment, libraryVersion, author }) {
  return {
    schema_version: 1,
    clone: slug,
    environment,
    library_version: libraryVersion,
    version: { number: 1, author, reason: 'Started a blank clone' },
    nodes: [],
    edges: [],
    groups: [],
    access_paths: [],
    credential_refs: [],
  }
}

/** Letters, digits and single hyphens. Empty when the text has none. */
export function cloneSlug(text) {
  const lower = text.trim().toLowerCase()
  let out = ''
  let dash = false
  for (const ch of lower) {
    const ok = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9')
    if (ok) {
      out += ch
      dash = false
    } else if (out && !dash) {
      out += '-'
      dash = true
    }
  }
  if (out.endsWith('-')) out = out.slice(0, -1)
  return out.slice(0, 63)
}

export function slugProblem(slug) {
  if (!slug) return 'Enter a short name.'
  const first = slug[0]
  if (first < 'a' || first > 'z') return 'Start the name with a letter.'
  return null
}

export function urlProblem(text) {
  if (!text) return null
  if (text.startsWith('https://') || text.startsWith('http://')) return null
  return 'Use a full address starting with http:// or https://.'
}

export function numberFromText(text) {
  const trimmed = text.trim()
  if (!trimmed) return { value: undefined }
  const value = Number(trimmed)
  if (!Number.isFinite(value)) return { error: 'Enter a number.' }
  return { value }
}

export function listFromText(text) {
  const items = []
  let current = ''
  for (const ch of text) {
    if (ch === ',') {
      const item = current.trim()
      if (item) items.push(item)
      current = ''
    } else current += ch
  }
  const last = current.trim()
  if (last) items.push(last)
  return items
}

const where = (layout) => (layout ? `${layout.x},${layout.y},${layout.width ?? ''},${layout.height ?? ''}` : '')

/** Changes when the shape of the drawing changes (items, nesting, connections, places), not when a property is typed. */
export function structureKey(document) {
  if (!document) return ''
  const nodes = document.nodes.map((n) => `${n.id}@${n.location || ''}@${where(n.layout)}`).join(',')
  const groups = document.groups.map((g) => `${g.id}@${g.parent || ''}@${where(g.layout)}`).join(',')
  const edges = document.edges.map((e) => `${e.from}:${e.type}:${e.to}`).join(',')
  return `${nodes}|${groups}|${edges}`
}

function usedIds(document) {
  return new Set([
    ...document.nodes.map((n) => n.id),
    ...document.groups.map((g) => g.id),
    ...document.access_paths.map((a) => a.id),
    ...document.credential_refs.map((c) => c.id),
    ...document.edges.map((e) => e.id).filter(Boolean),
  ])
}

function uniqueName(document, base) {
  const used = new Set([...document.nodes, ...document.groups].map((item) => item.name))
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base} ${n}`)) n += 1
  return `${base} ${n}`
}

function nextItemId(document, type) {
  const tail = type.id.split('.').pop().split('-').join('_')
  const prefix = type.family === 'boundaries' ? `g_${tail}` : `n_${tail}`
  const used = usedIds(document)
  let n = 1
  let id = `${prefix}_${n}`
  while (used.has(id)) {
    n += 1
    id = `${prefix}_${n}`
  }
  return id
}

function newItem(document, type) {
  const id = nextItemId(document, type)
  const name = uniqueName(document, type.display_name)
  const ref = `${type.id}@${type.version}`
  if (type.family === 'boundaries') {
    // A connection node holds only the name of its connector: its setup and its secrets stay with the connection on the server.
    const connection = isConnectionType(type.id) ? { properties: { connector: connectorKey(type.id) } } : {}
    const group = {
      id, type: ref, name, kind: isConnectionType(type.id) ? connectionKind(type.id) : BOUNDARY_KIND[type.id] || 'trust-zone',
      environment: document.environment, provenance: DRAWN, ...connection,
    }
    return { id, isGroup: true, document: { ...document, groups: [...document.groups, group] } }
  }
  const node = { id, type: ref, name, environment: document.environment, provenance: DRAWN }
  return { id, isGroup: false, document: { ...document, nodes: [...document.nodes, node] } }
}

/**
 * Place a library type on the clone. Boundaries become groups; everything else is a node.
 * Given a point (`at`, the middle of the new item, on the canvas) the item is drawn there, inside the group under it.
 * Given a group (`parentId`) it goes into the first free gap in that group. Either needs a placed clone.
 * `avoid` (default) keeps it off its neighbours; a drop with the pointer passes false and lands exactly where it was dropped.
 */
export function placeType(document, type, { at, parentId, avoid = true } = {}) {
  const made = newItem(document, type)
  if (!at && !parentId) return { document: made.document, id: made.id }
  const size = made.isGroup ? NEW_GROUP : { width: NODE_W, height: NODE_H }
  // A connection is a root with a lot inside it, so it settles in free space even when dropped on something.
  const spot = freeSpot(document, { size, parentId, near: at, avoid: avoid || isConnectionType(type.id), root: isConnectionType(type.id) })
  const origin = (parentId && absoluteBoxes(document).get(parentId)) || { x: 0, y: 0 }
  const put = (item) => {
    if (item.id !== made.id) return item
    const layout = { x: spot.x - origin.x, y: spot.y - origin.y, ...(made.isGroup ? size : {}) }
    return { ...item, ...(parentId ? { [made.isGroup ? 'parent' : 'location']: parentId } : {}), layout }
  }
  const placed = { ...made.document, nodes: made.document.nodes.map(put), groups: made.document.groups.map(put) }
  return { document: parentId ? growGroups(placed) : moveItems(placed, new Map([[made.id, spot]]), { onGrid: true }), id: made.id }
}

function writeItems(document, id, write) {
  let changed = false
  const map = (item) => {
    if (item.id !== id) return item
    const next = write(item)
    if (next !== item) changed = true
    return next
  }
  const nodes = document.nodes.map(map)
  const groups = document.groups.map(map)
  if (!changed) return document
  return { ...document, nodes, groups }
}

export function setName(document, id, name) {
  const trimmed = name.trim()
  if (!trimmed) return document
  return writeItems(document, id, (item) => (item.name === trimmed ? item : { ...item, name: trimmed }))
}

export function setProperty(document, id, key, value) {
  const blank = value === undefined || value === ''
  return writeItems(document, id, (item) => {
    const properties = { ...(item.properties || {}) }
    if (blank) {
      if (!(key in properties)) return item
      delete properties[key]
    } else if (properties[key] === value) return item
    else properties[key] = value
    const next = { ...item }
    if (Object.keys(properties).length) next.properties = properties
    else delete next.properties
    return next
  })
}

export function removeEdge(document, from, to, edgeType) {
  return {
    ...document,
    edges: document.edges.filter((edge) => !(edge.from === from && edge.to === to && edge.type === edgeType)),
  }
}

/** Remove an item. What it held is kept: those items move up into whatever held it, and stay where they are on screen. */
export function removeItem(document, id) {
  const gone = [...document.nodes, ...document.groups].find((item) => item.id === id)
  const up = gone ? parentOf(gone) : null
  const shift = gone?.layout || null
  const hoist = (item) => {
    if (parentOf(item) !== id) return item
    const key = item.location === id ? 'location' : 'parent'
    const { [key]: _held, ...rest } = item
    const layout = item.layout && shift ? { ...item.layout, x: item.layout.x + shift.x, y: item.layout.y + shift.y } : item.layout
    return { ...rest, ...(up ? { [key]: up } : {}), ...(layout ? { layout } : {}) }
  }
  return {
    ...document,
    nodes: document.nodes.filter((n) => n.id !== id).map(hoist),
    groups: document.groups.filter((g) => g.id !== id).map(hoist),
    edges: document.edges.filter((e) => e.from !== id && e.to !== id),
  }
}

/** Remove several items at once. Contents of a removed group stay, unless they are removed too. */
export function removeItems(document, ids) {
  return ids.reduce((doc, id) => removeItem(doc, id), document)
}

/**
 * Why this edge cannot be drawn, or null when it can.
 * A Generic relation is always allowed: it is the escape hatch when no typed edge fits (design B4a).
 */
export function edgeRefusal(sourceType, targetType, edgeType, { fromId, toId, edges } = {}) {
  if (fromId && toId && fromId === toId) return 'A connection has to join two different items.'
  const duplicate = (edges || []).some((e) => e.from === fromId && e.to === toId && e.type === edgeType)
  if (edgeType === GENERIC_RELATION) {
    return duplicate ? 'This Generic relation is already on the canvas.' : null
  }
  if (!sourceType || !targetType) return 'Both items need a type from the library before they can connect.'
  const reasons = []
  if (!(sourceType.edges?.allowed_out || []).includes(edgeType)) {
    reasons.push(`${sourceType.display_name} cannot start a ${humanKey(edgeType)} connection`)
  }
  if (!(targetType.edges?.allowed_in || []).includes(edgeType)) {
    reasons.push(`${targetType.display_name} cannot receive a ${humanKey(edgeType)} connection`)
  }
  if (!reasons.length) return duplicate ? 'This connection is already on the canvas.' : null
  const detail = reasons.length === 1 ? `${reasons[0]}.` : `${reasons[0]}, and ${reasons[1]}.`
  return `${detail} A Generic relation is allowed instead.`
}

/** The first typed edge both ends allow, or a Generic relation when none does. */
export function firstFit(sourceType, targetType, ends) {
  const fit = EDGE_TYPES.find((type) => type !== GENERIC_RELATION && !edgeRefusal(sourceType, targetType, type, ends))
  return fit || GENERIC_RELATION
}

export function addEdge(document, from, to, edgeType, types) {
  const source = [...document.nodes, ...document.groups].find((item) => item.id === from)
  const target = [...document.nodes, ...document.groups].find((item) => item.id === to)
  const refusal = edgeRefusal(types.get(source?.type), types.get(target?.type), edgeType, {
    fromId: from, toId: to, edges: document.edges,
  })
  if (refusal) return { refusal }
  return { document: { ...document, edges: [...document.edges, { from, to, type: edgeType, provenance: DRAWN }] } }
}

const sameEdge = (a, b) => a.from === b.from && a.to === b.to && a.type === b.type

/** Change the kind of an existing connection, under the same rules as drawing a new one. */
export function setEdgeType(document, edge, edgeType, types) {
  if (edge.type === edgeType) return { document }
  const items = [...document.nodes, ...document.groups]
  const typeOf = (id) => types.get(items.find((item) => item.id === id)?.type)
  const others = document.edges.filter((entry) => !sameEdge(entry, edge))
  const refusal = edgeRefusal(typeOf(edge.from), typeOf(edge.to), edgeType, { fromId: edge.from, toId: edge.to, edges: others })
  if (refusal) return { refusal }
  return { document: { ...document, edges: document.edges.map((entry) => (sameEdge(entry, edge) ? { ...entry, type: edgeType } : entry)) } }
}

/** Set a plain field of a node or group (owner, notes, criticality). An empty value removes it. */
export function setField(document, id, key, value) {
  const blank = value === undefined || value === '' || (Array.isArray(value) && !value.length)
  return writeItems(document, id, (item) => {
    if (blank) {
      if (!(key in item)) return item
      const { [key]: _gone, ...rest } = item
      return rest
    }
    return JSON.stringify(item[key]) === JSON.stringify(value) ? item : { ...item, [key]: value }
  })
}

/**
 * Wrap the chosen items in a new container of the given boundary type.
 * They must share a container, so the result is one clear step up in the nesting.
 */
export function groupItems(document, ids, type) {
  const roots = topOnes(document, ids)
  if (!roots.length) return { refusal: 'Select the items to group first.' }
  const all = [...document.nodes, ...document.groups]
  const parents = new Set(roots.map((id) => parentOf(all.find((item) => item.id === id))))
  if (parents.size > 1) return { refusal: 'These items sit in different containers. Move them into one container first.' }
  const made = newItem(document, type)
  if (!made.isGroup) return { refusal: `${type.display_name} cannot hold other items. Choose a boundary type.` }
  const parentId = [...parents][0]
  const boxes = absoluteBoxes(document)
  const wrap = boxAround(boxes, roots)
  const origin = (parentId && boxes.get(parentId)) || { x: 0, y: 0 }
  const held = new Set(roots)
  const put = (item) => {
    if (item.id === made.id) {
      const layout = { x: wrap.x - origin.x, y: wrap.y - origin.y, width: wrap.width, height: wrap.height }
      return { ...item, ...(parentId ? { parent: parentId } : {}), layout }
    }
    if (!held.has(item.id)) return item
    const box = boxes.get(item.id)
    const key = made.document.groups.some((g) => g.id === item.id) ? 'parent' : 'location'
    return { ...item, [key]: made.id, layout: { ...item.layout, x: box.x - wrap.x, y: box.y - wrap.y } }
  }
  const next = { ...made.document, nodes: made.document.nodes.map(put), groups: made.document.groups.map(put) }
  return { document: growGroups(next), id: made.id }
}
