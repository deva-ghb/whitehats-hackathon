import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  groupItems, placeType, removeItem, removeItems, setEdgeType, setField,
} from '../src/studio/edit.js'
import { GRID, NEW_GROUP, NODE_H, NODE_W, absoluteBoxes } from '../src/studio/geometry.js'
import { TYPES, clone, find } from './fixtures.mjs'

const backend = { ...TYPES.get('service.backend@1') }
const vpc = { ...TYPES.get('boundary.vpc@1') }
const account = { ...TYPES.get('boundary.aws-account@1') }

test('a type dropped on the canvas is drawn where it was dropped', () => {
  const { document, id } = placeType(clone(), backend, { at: { x: 1000, y: 600 } })
  const box = absoluteBoxes(document).get(id)
  assert.ok(Math.abs(box.x + NODE_W / 2 - 1000) <= GRID / 2, 'across, to the nearest grid line')
  assert.ok(Math.abs(box.y + NODE_H / 2 - 600) <= GRID / 2, 'down, to the nearest grid line')
  assert.equal(box.x % GRID, 0)
  assert.equal(find(document, id).provenance.kind, 'drawn')
  assert.equal('location' in find(document, id), false)
})

test('a type dropped on a group goes into that group', () => {
  const { document, id } = placeType(clone(), backend, { at: { x: 400, y: 340 } })
  assert.equal(find(document, id).location, 'g_vpc')
})

test('a boundary dropped on the canvas is a group with a size', () => {
  const { document, id } = placeType(clone(), vpc, { at: { x: 1000, y: 700 } })
  assert.equal(find(document, id).kind, 'vpc')
  assert.equal(find(document, id).layout.width, NEW_GROUP.width)
})

test('placing into a named group uses a free gap and the group grows if it is full', () => {
  let doc = clone()
  let last
  for (let n = 0; n < 4; n += 1) {
    const placed = placeType(doc, backend, { parentId: 'g_vpc' })
    doc = placed.document
    last = placed.id
  }
  const boxes = absoluteBoxes(doc)
  const group = boxes.get('g_vpc')
  for (const node of doc.nodes.filter((n) => n.location === 'g_vpc')) {
    const box = boxes.get(node.id)
    assert.ok(box.x + box.width <= group.x + group.width && box.y + box.height <= group.y + group.height, `${node.id} is inside`)
  }
  const spots = doc.nodes.filter((n) => n.location === 'g_vpc').map((n) => `${n.layout.x},${n.layout.y}`)
  assert.equal(new Set(spots).size, spots.length, 'no two share a spot')
  assert.equal(find(doc, last).location, 'g_vpc')
})

test('placing with no point and no group still gives a plain item the way it did before', () => {
  const { document, id } = placeType(clone(), backend)
  assert.equal(find(document, id).layout, undefined)
})

test('changing a connection keeps its two ends and refuses what the library forbids', () => {
  const doc = clone({ edges: [{ from: 'n_api', to: 'n_db', type: 'reads' }] })
  const out = setEdgeType(doc, doc.edges[0], 'generic_relation', TYPES)
  assert.deepEqual(out.document.edges, [{ from: 'n_api', to: 'n_db', type: 'generic_relation' }])
  const refused = setEdgeType(doc, doc.edges[0], 'calls', TYPES)
  assert.match(refused.refusal, /RDS cannot receive a Calls connection/)
  assert.equal(refused.document, undefined)
})

test('a connection cannot be changed into one that is already drawn between the same two', () => {
  const doc = clone({ edges: [{ from: 'n_api', to: 'n_db', type: 'reads' }, { from: 'n_api', to: 'n_db', type: 'writes' }] })
  assert.match(setEdgeType(doc, doc.edges[1], 'reads', TYPES).refusal, /already on the canvas/)
})

test('owner and notes are set on a node or a group, and an empty value removes them', () => {
  let doc = setField(clone(), 'n_api', 'owner', 'Platform team')
  doc = setField(doc, 'g_vpc', 'notes', 'Holds the app.')
  assert.equal(find(doc, 'n_api').owner, 'Platform team')
  assert.equal(find(doc, 'g_vpc').notes, 'Holds the app.')
  doc = setField(doc, 'n_api', 'owner', '')
  assert.equal('owner' in find(doc, 'n_api'), false)
  const same = setField(doc, 'g_vpc', 'notes', 'Holds the app.')
  assert.equal(same, doc)
})

test('removing a group keeps what it held, in the same place on screen', () => {
  const before = absoluteBoxes(clone()).get('n_api')
  const doc = removeItem(clone(), 'g_vpc')
  assert.equal(find(doc, 'g_vpc'), undefined)
  assert.equal('location' in find(doc, 'n_api'), false)
  assert.deepEqual(absoluteBoxes(doc).get('n_api'), before)
})

test('removing a connection end removes the connection, and removing several is one step', () => {
  const doc = clone({ edges: [{ from: 'n_api', to: 'n_db', type: 'reads' }] })
  const out = removeItems(doc, ['n_db', 'n_web'])
  assert.deepEqual(out.edges, [])
  assert.deepEqual(out.nodes.map((n) => n.id), ['n_api'])
})

test('grouping wraps the chosen items in a new container that holds them all', () => {
  const { document, id } = groupItems(clone(), ['n_db', 'n_web'], account)
  const group = find(document, id)
  assert.equal(group.kind, 'account')
  assert.equal(find(document, 'n_db').location, id)
  assert.equal(find(document, 'n_web').location, id)
  const boxes = absoluteBoxes(document)
  for (const nodeId of ['n_db', 'n_web']) {
    const inner = boxes.get(nodeId)
    const outer = boxes.get(id)
    assert.ok(inner.x >= outer.x && inner.y >= outer.y + 44 && inner.x + inner.width <= outer.x + outer.width && inner.y + inner.height <= outer.y + outer.height)
  }
  assert.deepEqual(boxes.get('n_db'), absoluteBoxes(clone()).get('n_db'))
})

test('items from different containers are not grouped, and a service is not a container', () => {
  const refused = groupItems(clone(), ['n_api', 'n_db'], account)
  assert.match(refused.refusal, /different containers/)
  assert.match(groupItems(clone(), ['n_db'], backend).refusal, /cannot hold other items/)
  assert.match(groupItems(clone(), [], account).refusal, /Select the items/)
})

test('a drop with the pointer lands exactly where it was dropped, even over a container edge', () => {
  const { document, id } = placeType(clone(), backend, { at: { x: 420, y: 90 }, avoid: false })
  const box = absoluteBoxes(document).get(id)
  assert.ok(Math.abs(box.x + NODE_W / 2 - 420) <= GRID / 2)
  assert.ok(Math.abs(box.y + NODE_H / 2 - 90) <= GRID / 2)
})
