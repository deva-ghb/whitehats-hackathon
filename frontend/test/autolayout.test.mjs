import assert from 'node:assert/strict'
import { test } from 'node:test'
import { PLAN_SPACING, autoBoxes, placesFor } from '../src/studio/autolayout.js'
import { absoluteBoxes, parentOf } from '../src/studio/geometry.js'
import { SCENE_SPACING } from '../src/studio/scene/placement.js'
import { TYPES, clone } from './fixtures.mjs'

/** An estate with no stored places: an account holding two networks, one of them nested, with `perNetwork` nodes in each. */
function estate(perNetwork) {
  const groups = [
    { id: 'g_account', type: 'boundary.aws-account@1', name: 'Account', environment: 'prod' },
    { id: 'g_one', type: 'boundary.vpc@1', name: 'Network one', environment: 'prod', location: 'g_account' },
    { id: 'g_two', type: 'boundary.vpc@1', name: 'Network two', environment: 'prod', location: 'g_account' },
    { id: 'g_inner', type: 'boundary.vpc@1', name: 'Inner', environment: 'prod', location: 'g_one' },
  ]
  const nodes = []
  const edges = []
  for (const [group, count] of [['g_one', perNetwork], ['g_two', perNetwork], ['g_inner', Math.ceil(perNetwork / 2)], ['g_account', 3]]) {
    for (let i = 0; i < count; i += 1) {
      const kind = i % 3 === 0 ? 'data.rds@1' : 'service.backend@1'
      nodes.push({ id: `n_${group}_${i}`, type: kind, name: `${group}-${kind.slice(0, 4)}-${String(i).padStart(3, '0')}`, environment: 'prod', location: group })
      if (i > 0) edges.push({ from: `n_${group}_${i - 1}`, to: `n_${group}_${i}`, type: 'calls' })
    }
  }
  return clone({ groups, nodes, edges })
}

const SPACINGS = [['plan', PLAN_SPACING], ['scene', SCENE_SPACING]]
const overlap = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
const inside = (inner, outer) => inner.x >= outer.x && inner.y >= outer.y && inner.x + inner.width <= outer.x + outer.width && inner.y + inner.height <= outer.y + outer.height
const extent = (boxes) => {
  const all = [...boxes.values()]
  const left = Math.min(...all.map((box) => box.x))
  const top = Math.min(...all.map((box) => box.y))
  return { width: Math.max(...all.map((box) => box.x + box.width)) - left, height: Math.max(...all.map((box) => box.y + box.height)) - top }
}

for (const [view, spacing] of SPACINGS) {
  for (const per of [9, 150]) {
    const document = estate(per)
    const boxes = autoBoxes(document, TYPES, spacing)

    test(`${view}: every item of a ${document.nodes.length} node clone gets a place, and the same clone always gets the same places`, () => {
      for (const item of [...document.groups, ...document.nodes]) assert.ok(boxes.has(item.id), `${item.id} has a place`)
      assert.deepEqual([...autoBoxes(document, TYPES, spacing)], [...boxes])
      assert.deepEqual([...autoBoxes(clone({ ...document, nodes: [...document.nodes].reverse() }), TYPES, spacing)].sort(), [...boxes].sort())
    })

    test(`${view}: no two nodes overlap in a ${document.nodes.length} node clone, and two containers overlap only when one holds the other`, () => {
      const nodes = document.nodes.map((node) => [node.id, boxes.get(node.id)])
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) assert.ok(!overlap(nodes[i][1], nodes[j][1]), `${nodes[i][0]} over ${nodes[j][0]}`)
      }
      const family = (id) => { const up = [id]; for (let at = id; ;) { const parent = parentOf([...document.groups].find((g) => g.id === at)); if (!parent) break; up.push(parent); at = parent } return up }
      for (let i = 0; i < document.groups.length; i += 1) {
        for (let j = i + 1; j < document.groups.length; j += 1) {
          const [a, b] = [document.groups[i].id, document.groups[j].id]
          if (family(a).includes(b) || family(b).includes(a)) continue
          assert.ok(!overlap(boxes.get(a), boxes.get(b)), `${a} over ${b}`)
        }
      }
    })

    test(`${view}: a container holds all of its children, down to the nested one, in a ${document.nodes.length} node clone`, () => {
      for (const item of [...document.groups, ...document.nodes]) {
        const parent = parentOf(item)
        if (parent) assert.ok(inside(boxes.get(item.id), boxes.get(parent)), `${item.id} inside ${parent}`)
      }
    })

    test(`${view}: a ${document.nodes.length} node clone is laid out in a picture of a reasonable shape, never one column`, () => {
      const shape = extent(boxes)
      const ratio = shape.width / shape.height
      assert.ok(ratio >= 0.4 && ratio <= 4, `width over height is ${ratio.toFixed(2)}`)
      const columns = new Set(document.nodes.map((node) => boxes.get(node.id).x))
      assert.ok(columns.size > 1, 'more than one column')
    })
  }
}

test('a clone that has stored places is drawn at them, and nothing is moved by the automatic layout', () => {
  const placed = clone()
  assert.deepEqual([...placesFor(placed, TYPES)], [...absoluteBoxes(placed)])
})

test('a clone with no items still has an estate to draw on', () => {
  const empty = clone({ groups: [], nodes: [], edges: [] })
  assert.equal(autoBoxes(empty, TYPES).size, 0)
})
