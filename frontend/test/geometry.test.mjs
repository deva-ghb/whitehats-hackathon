import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  GRID, GROUP_PAD, NODE_H, NODE_W, absoluteBoxes, alignItems, boxAround, clearLayout, descendants, distributeItems, freeSpot,
  freezeLayout, isPlaced, moveItems, nudgeItems, reparentItem, resizeGroup, topOnes,
} from '../src/studio/geometry.js'
import { clone, find } from './fixtures.mjs'

const contains = (outer, inner) => inner.x >= outer.x && inner.y >= outer.y
  && inner.x + inner.width <= outer.x + outer.width && inner.y + inner.height <= outer.y + outer.height

test('a position inside a group is counted from the corner of that group', () => {
  const boxes = absoluteBoxes(clone())
  assert.deepEqual(boxes.get('n_api'), { x: 124, y: 148, width: NODE_W, height: NODE_H })
  assert.deepEqual(boxes.get('g_vpc'), { x: 100, y: 100, width: 400, height: 300 })
})

test('a clone is placed only when every item has a stored position', () => {
  assert.equal(isPlaced(clone()), true)
  const missing = clone()
  delete missing.nodes[1].layout
  assert.equal(isPlaced(missing), false)
})

test('freezing stores what was drawn, relative to the group, and leaves a placed clone alone', () => {
  const unplaced = clearLayout(clone())
  const drawn = new Map([
    ['g_vpc', { x: 100, y: 100, width: 400, height: 300 }],
    ['n_api', { x: 124, y: 148, width: NODE_W, height: NODE_H }],
    ['n_db', { x: 700, y: 120, width: NODE_W, height: NODE_H }],
    ['n_web', { x: 700, y: 320, width: NODE_W, height: NODE_H }],
  ])
  const frozen = freezeLayout(unplaced, drawn)
  assert.deepEqual(find(frozen, 'n_api').layout, { x: 24, y: 48 })
  assert.deepEqual(find(frozen, 'g_vpc').layout, { x: 100, y: 100, width: 400, height: 300 })
  const placed = clone()
  assert.equal(freezeLayout(placed, drawn), placed)
})

test('moving a group moves what is inside it, on screen', () => {
  const moved = moveItems(clone(), new Map([['g_vpc', { x: 160, y: 140 }]]))
  assert.deepEqual(absoluteBoxes(moved).get('n_api'), { x: 184, y: 188, width: NODE_W, height: NODE_H })
  assert.deepEqual(find(moved, 'n_api').layout, { x: 24, y: 48 })
})

test('dropping a node with its middle inside a group puts it in that group', () => {
  const moved = moveItems(clone(), new Map([['n_db', { x: 160, y: 200 }]]))
  assert.equal(find(moved, 'n_db').location, 'g_vpc')
  assert.deepEqual(find(moved, 'n_db').layout, { x: 60, y: 100 })
})

test('dragging a node out of its group takes it out of that group', () => {
  const moved = moveItems(clone(), new Map([['n_api', { x: 900, y: 600 }]]))
  assert.equal('location' in find(moved, 'n_api'), false)
  assert.deepEqual(find(moved, 'n_api').layout, { x: 900, y: 600 })
})

test('a node dropped half outside its group makes the group grow to hold it', () => {
  const moved = moveItems(clone(), new Map([['n_api', { x: 300, y: 300 }]]))
  const group = absoluteBoxes(moved).get('g_vpc')
  const inner = absoluteBoxes(moved).get('n_api')
  assert.equal(find(moved, 'n_api').location, 'g_vpc')
  assert.ok(contains(group, inner), 'the group holds its child')
})

test('a node cannot sit over the title strip or past the left edge of its group', () => {
  const moved = moveItems(clone(), new Map([['n_api', { x: 90, y: 90 }]]))
  assert.deepEqual(find(moved, 'n_api').layout, { x: GROUP_PAD.side, y: GROUP_PAD.top })
})

test('a group cannot be dropped inside itself or inside what it holds', () => {
  const nested = clone()
  nested.groups.push({ id: 'g_sub', type: 'boundary.vpc@1', name: 'Subnet', kind: 'subnet', environment: 'prod', parent: 'g_vpc', layout: { x: 20, y: 60, width: 200, height: 120 } })
  // the middle of the VPC lands inside its own subnet
  const moved = moveItems(nested, new Map([['g_vpc', { x: 0, y: 50 }]]))
  assert.equal('parent' in find(moved, 'g_vpc'), false)
  assert.equal(find(moved, 'g_sub').parent, 'g_vpc')
  assert.deepEqual(descendants(nested, 'g_vpc'), new Set(['g_sub', 'n_api']))
})

test('only the outermost of a selection is moved, because the rest go along', () => {
  assert.deepEqual(topOnes(clone(), ['g_vpc', 'n_api', 'n_db']).sort(), ['g_vpc', 'n_db'])
})

test('nudging keeps an item in its group and does not push it past the padding', () => {
  const left = nudgeItems(clone(), ['n_api'], -400, 0)
  assert.equal(find(left, 'n_api').location, 'g_vpc')
  assert.equal(find(left, 'n_api').layout.x, GROUP_PAD.side)
  const right = nudgeItems(clone(), ['n_db'], 16, 8)
  assert.deepEqual(find(right, 'n_db').layout, { x: 716, y: 128 })
})

test('shrinking a group never cuts off what it holds', () => {
  const resized = resizeGroup(clone(), 'g_vpc', { x: 100, y: 100, width: 120, height: 100 })
  const boxes = absoluteBoxes(resized)
  assert.ok(contains(boxes.get('g_vpc'), boxes.get('n_api')))
})

test('resizing from the top left corner leaves the contents where they were on screen', () => {
  const resized = resizeGroup(clone(), 'g_vpc', { x: 60, y: 60, width: 440, height: 340 })
  assert.deepEqual(absoluteBoxes(resized).get('n_api'), { x: 124, y: 148, width: NODE_W, height: NODE_H })
  assert.deepEqual(find(resized, 'g_vpc').layout, { x: 60, y: 60, width: 440, height: 340 })
})

test('aligning to the left edge lines up the left sides', () => {
  const aligned = alignItems(clone(), ['n_db', 'n_web', 'n_api'], 'left')
  const boxes = absoluteBoxes(aligned)
  assert.equal(boxes.get('n_db').x, 124)
  assert.equal(boxes.get('n_web').x, 124)
})

test('aligning on the middle line lines up the centres', () => {
  const aligned = alignItems(clone(), ['n_db', 'n_web'], 'middle')
  const boxes = absoluteBoxes(aligned)
  assert.equal(boxes.get('n_db').y + NODE_H / 2, boxes.get('n_web').y + NODE_H / 2)
})

test('aligning one item, or none, changes nothing', () => {
  const same = clone()
  assert.equal(alignItems(same, ['n_db'], 'left'), same)
})

test('distributing leaves equal gaps between three items', () => {
  const spread = clone()
  spread.nodes.push({ id: 'n_c', type: 'service.backend@1', name: 'c', environment: 'prod', layout: { x: 700, y: 700 } })
  const done = distributeItems(spread, ['n_db', 'n_web', 'n_c'], 'vertical')
  const boxes = absoluteBoxes(done)
  const gap1 = boxes.get('n_web').y - (boxes.get('n_db').y + NODE_H)
  const gap2 = boxes.get('n_c').y - (boxes.get('n_web').y + NODE_H)
  assert.equal(Math.abs(gap1 - gap2) <= 1, true)
})

test('a free spot does not cover a neighbour, and inside a group it is the next gap', () => {
  const onTop = freeSpot(clone(), { size: { width: NODE_W, height: NODE_H }, near: { x: 700 + NODE_W / 2, y: 120 + NODE_H / 2 } })
  assert.notDeepEqual(onTop, { x: 700, y: 120 })
  const inside = freeSpot(clone(), { size: { width: NODE_W, height: NODE_H }, parentId: 'g_vpc' })
  const taken = absoluteBoxes(clone()).get('n_api')
  const wanted = { ...inside, width: NODE_W, height: NODE_H }
  const apart = wanted.x >= taken.x + taken.width || wanted.y >= taken.y + taken.height
  assert.equal(apart, true)
})

test('moving an item into another group puts it in a free place there', () => {
  const { document } = reparentItem(clone(), 'n_db', 'g_vpc')
  assert.equal(find(document, 'n_db').location, 'g_vpc')
  const boxes = absoluteBoxes(document)
  assert.ok(contains(boxes.get('g_vpc'), boxes.get('n_db')))
  const out = reparentItem(document, 'n_db', null).document
  assert.equal('location' in find(out, 'n_db'), false)
  assert.deepEqual(absoluteBoxes(out).get('n_db'), boxes.get('n_db'))
})

test('a group is refused as its own container', () => {
  const nested = clone()
  nested.groups.push({ id: 'g_sub', type: 'boundary.vpc@1', name: 'Subnet', kind: 'subnet', environment: 'prod', parent: 'g_vpc', layout: { x: 20, y: 60, width: 200, height: 120 } })
  assert.match(reparentItem(nested, 'g_vpc', 'g_sub').refusal, /cannot go inside itself/)
  assert.match(reparentItem(nested, 'g_vpc', 'g_vpc').refusal, /cannot go inside itself/)
})

test('the box around a selection leaves room for the title and is on the grid', () => {
  const boxes = absoluteBoxes(clone())
  const wrap = boxAround(boxes, ['n_db', 'n_web'])
  assert.equal(wrap.x % GRID, 0)
  assert.equal(wrap.width % GRID, 0)
  assert.ok(contains(wrap, boxes.get('n_db')) && contains(wrap, boxes.get('n_web')))
  assert.ok(boxes.get('n_db').y - wrap.y >= GROUP_PAD.top)
})

test('a drop on the grid settles at a multiple of the grid counted from the group it lands in', () => {
  const moved = moveItems(clone(), new Map([['n_db', { x: 163, y: 207 }]]), { onGrid: true })
  assert.equal(find(moved, 'n_db').location, 'g_vpc')
  assert.equal(find(moved, 'n_db').layout.x % GRID, 0)
  assert.equal(find(moved, 'n_db').layout.y % GRID, 0)
})

test('a group that has to grow grows to a whole number of grid squares', () => {
  const moved = moveItems(clone(), new Map([['n_api', { x: 300, y: 350 }]]))
  const layout = find(moved, 'g_vpc').layout
  assert.equal(layout.width % GRID, 0)
  assert.equal(layout.height % GRID, 0)
})
