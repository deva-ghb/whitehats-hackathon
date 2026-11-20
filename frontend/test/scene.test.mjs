import assert from 'node:assert/strict'
import { test } from 'node:test'
import { NODE_H, NODE_W } from '../src/studio/geometry.js'
import { hull, insideConvex, pickAt, pickEdge, pickWithin, rectsOverlap } from '../src/studio/scene/hit.js'
import { buildScene, drawnEdges, paintOrder } from '../src/studio/scene/model.js'
import { outsideSide, platformBox, sceneBoxes } from '../src/studio/scene/placement.js'
import {
  BASE, COS30, SCALE, UNIT_X, UNIT_Y, fitView, groundToScene, groundToScreen, project, sceneToGround, screenToGround, unproject, zoomAbout,
} from '../src/studio/scene/project.js'
import { recipeName, solid } from '../src/studio/scene/recipes.js'
import { clearLayout } from '../src/studio/geometry.js'
import { TYPES, clone } from './fixtures.mjs'

const close = (a, b, message) => assert.ok(Math.abs(a - b) < 1e-9, `${message || ''} ${a} vs ${b}`)

test('the projection follows the website kit: x runs right and down, y left and down, z up', () => {
  assert.deepEqual(project(0, 0, 0), [0, 0])
  const [x, y] = project(1, 0, 0)
  close(x, COS30 * SCALE)
  close(y, 0.5 * SCALE)
  const [lx, ly] = project(0, 1, 0)
  close(lx, -COS30 * SCALE)
  close(ly, 0.5 * SCALE)
  assert.deepEqual(project(0, 0, 2), [0, -2 * SCALE])
})

test('ground to screen and back is exact, at every height and every view', () => {
  const views = [{ x: 0, y: 0, zoom: 1 }, { x: 313.7, y: -88.2, zoom: 0.37 }, { x: -1200, y: 640, zoom: 2.9 }]
  for (const view of views) {
    for (const lift of [0, 1.5, -0.25]) {
      for (const [gx, gy] of [[0, 0], [232, 72], [-640, 910], [12345.5, -777.25], [1, 1]]) {
        const [px, py] = groundToScreen(view, gx, gy, lift)
        const back = screenToGround(view, px, py, lift)
        close(back.x, gx, `x for ${gx},${gy}`)
        close(back.y, gy, `y for ${gx},${gy}`)
      }
    }
  }
})

test('unproject is the inverse of project on any plane', () => {
  for (const z of [0, 0.5, 3]) {
    const [x, y] = project(3.25, -1.5, z)
    const [u, v] = unproject(x, y, z)
    close(u, 3.25)
    close(v, -1.5)
  }
  const [sx, sy] = groundToScene(640, 320)
  const g = sceneToGround(sx, sy)
  close(g.x, 640)
  close(g.y, 320)
})

test('a pointer over the top of the platform lands on the plane things stand on', () => {
  const [sx, sy] = project(2, 3, BASE)
  const g = sceneToGround(sx, sy)
  close(g.x, 2 * UNIT_X)
  close(g.y, 3 * UNIT_Y)
})

test('zooming about a point keeps that point still', () => {
  const view = { x: 40, y: 25, zoom: 1 }
  const before = screenToGround(view, 300, 200)
  const next = zoomAbout(view, 300, 200, 2.2)
  const after = screenToGround(next, 300, 200)
  close(before.x, after.x)
  close(before.y, after.y)
  assert.equal(next.zoom, 2.2)
  assert.equal(zoomAbout(view, 0, 0, 99).zoom, 3)
  assert.equal(zoomAbout(view, 0, 0, 0.001).zoom, 0.08)
})

test('fit centres the drawing and never zooms in past the limit', () => {
  const view = fitView({ x: 100, y: 50, width: 200, height: 100 }, { width: 1000, height: 800 }, { margin: 0, maxZoom: 1.4 })
  assert.equal(view.zoom, 1.4)
  close(view.x + 200 * view.zoom, 500)
  close(view.y + 100 * view.zoom, 400)
  const small = fitView({ x: 0, y: 0, width: 4000, height: 2000 }, { width: 800, height: 600 }, { margin: 0 })
  close(small.zoom, 0.2)
})

// ---------------------------------------------------------------- hit testing

const square = (x, y, size) => [[x, y], [x + size, y], [x + size, y + size], [x, y + size]]
const thing = (id, polygon) => ({ id, outline: polygon, bounds: { x: Math.min(...polygon.map((p) => p[0])), y: Math.min(...polygon.map((p) => p[1])), width: Math.max(...polygon.map((p) => p[0])) - Math.min(...polygon.map((p) => p[0])), height: Math.max(...polygon.map((p) => p[1])) - Math.min(...polygon.map((p) => p[1])) } })

test('a point is inside a convex outline, on its edge, and not outside it', () => {
  const box = square(0, 0, 10)
  assert.equal(insideConvex(box, 5, 5), true)
  assert.equal(insideConvex(box, 0, 5), true)
  assert.equal(insideConvex(box, 11, 5), false)
  assert.equal(insideConvex(box, -0.1, -0.1), false)
})

test('the hull of scattered points holds all of them', () => {
  const points = [[0, 0], [4, 0], [4, 4], [0, 4], [2, 2], [1, 3], [3, 1]]
  const outline = hull(points)
  assert.equal(outline.length, 4)
  for (const [x, y] of points) assert.equal(insideConvex(outline, x, y), true)
})

test('the topmost thing under the point wins, and a miss is null', () => {
  const things = [thing('back', square(0, 0, 100)), thing('front', square(50, 50, 100))]
  assert.equal(pickAt(things, 75, 75), 'front')
  assert.equal(pickAt(things, 10, 10), 'back')
  assert.equal(pickAt(things, 400, 400), null)
})

test('a marquee takes everything it touches', () => {
  const things = [thing('a', square(0, 0, 10)), thing('b', square(100, 100, 10)), thing('c', square(20, 0, 10))]
  assert.deepEqual(pickWithin(things, { x: -5, y: -5, width: 40, height: 20 }).sort(), ['a', 'c'])
})

test('the nearest connection within reach is picked', () => {
  const edges = [{ id: 'one', points: [[0, 0], [100, 0]] }, { id: 'two', points: [[0, 50], [100, 50]] }]
  assert.equal(pickEdge(edges, 40, 4, 6), 'one')
  assert.equal(pickEdge(edges, 40, 47, 6), 'two')
  assert.equal(pickEdge(edges, 40, 25, 6), null)
})

test('hitting a drawn node: the solid is found from a point on its top, and not from beside it', () => {
  const drawn = solid('backend', { tone: 'w' })
  const things = [thing('n', drawn.outline)]
  const [tx, ty] = drawn.top
  assert.equal(pickAt(things, tx, ty - 4), 'n')
  assert.equal(pickAt(things, tx + 400, ty), null)
})

// ---------------------------------------------------------------- the scene model

const types = new Map([...TYPES, ['edge.alb@1', { id: 'edge.alb', version: 1, family: 'network', display_name: 'ALB', properties: [], edges: { allowed_out: [], allowed_in: [] } }],
  ['identity.okta@1', { id: 'identity.okta', version: 1, family: 'identity', display_name: 'Okta', properties: [], edges: { allowed_out: [], allowed_in: [] } }]])

test('each node family is its own solid', () => {
  assert.equal(recipeName('service.backend', 'services'), 'backend')
  assert.equal(recipeName('service.function', 'services'), 'function')
  assert.equal(recipeName('data.rds', 'data'), 'data')
  assert.equal(recipeName('data.s3', 'data'), 'object')
  assert.equal(recipeName('messaging.sqs', 'messaging'), 'queue')
  assert.equal(recipeName('edge.alb', 'network'), 'edge')
  assert.equal(recipeName('identity.okta', 'identity'), 'person')
  assert.equal(recipeName('identity.kms', 'identity'), 'vault')
  assert.equal(recipeName('repo.github', 'code_and_delivery'), 'repo')
  assert.equal(recipeName('saas.stripe', 'saas'), 'saas')
  assert.equal(recipeName('generic.data', 'data'), 'generic')
  assert.equal(recipeName('nothing.here', undefined), 'generic')
})

test('a person floats above its place, with a drop line; a box stands on it', () => {
  const person = solid('person', { tone: 'w' })
  const box = solid('backend', { tone: 'w' })
  assert.equal(person.floats, true)
  assert.equal(box.floats, false)
  assert.ok(person.markup.includes('class="drop"'))
  assert.ok(solid('person', { tone: 'w', detail: false }).markup.indexOf('class="drop"') < 0)
})

test('zoomed out, a node is a plain box of the same size', () => {
  const full = solid('vault', { tone: 'w', detail: true })
  const plain = solid('vault', { tone: 'w', detail: false })
  assert.ok(full.markup.includes('glyph'))
  assert.ok(!plain.markup.includes('glyph'))
  assert.ok(plain.markup.length < full.markup.length)
})

test('the platform wraps what stands inside and leaves out what stands outside', () => {
  const doc = clone()
  doc.nodes.push({ id: 'n_lb', type: 'edge.alb@1', name: 'alb', environment: 'prod', layout: { x: -900, y: 100 } })
  const boxes = sceneBoxes(doc, types)
  const platform = platformBox(doc, boxes, types)
  assert.ok(platform.x > -900 + NODE_W, 'the public edge is outside')
  assert.ok(platform.x <= 100 && platform.y <= 100)
  assert.ok(platform.x + platform.width >= 700 + NODE_W)
  assert.equal(outsideSide({ type: 'edge.alb@1' }, types), 'left')
})

test('an empty clone has a blank platform to drop onto', () => {
  const doc = clone({ nodes: [], groups: [], edges: [] })
  const platform = platformBox(doc, sceneBoxes(doc, types), types)
  assert.ok(platform.width > 0 && platform.height > 0)
})

test('a clone with no places gets a place for every item, none on another, and a group holds its own', () => {
  const doc = clearLayout(clone())
  doc.nodes.push({ id: 'n_lb', type: 'edge.alb@1', name: 'alb', environment: 'prod' })
  doc.nodes.push({ id: 'n_who', type: 'identity.okta@1', name: 'who', environment: 'prod', location: 'g_vpc' })
  const boxes = sceneBoxes(doc, types)
  assert.equal(boxes.size, doc.nodes.length + doc.groups.length)
  const ids = [...boxes.keys()]
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const a = boxes.get(ids[i])
      const b = boxes.get(ids[j])
      const nested = ids[i] === 'g_vpc' || ids[j] === 'g_vpc'
      if (nested) continue
      const apart = a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y
      assert.ok(apart, `${ids[i]} and ${ids[j]} overlap`)
    }
  }
  const group = boxes.get('g_vpc')
  for (const id of ['n_api', 'n_who']) {
    const box = boxes.get(id)
    assert.ok(box.x >= group.x && box.y >= group.y && box.x + box.width <= group.x + group.width && box.y + box.height <= group.y + group.height, `${id} inside its group`)
  }
  const platform = platformBox(doc, boxes, types)
  const lb = boxes.get('n_lb')
  assert.ok(lb.x + lb.width <= platform.x, 'the public edge stands outside, to the left')
})

test('the scene paints what is behind first, even when a long box sits beside a short one', () => {
  const long = { id: 'long', foot: { x0: 0, x1: 4, y0: 0, y1: 1 }, bounds: { x: 0, y: 0, width: 200, height: 100 } }
  const front = { id: 'front', foot: { x0: 0.2, x1: 0.8, y0: 1.1, y1: 1.7 }, bounds: { x: 0, y: 0, width: 200, height: 100 } }
  assert.deepEqual(paintOrder([front, long]).map((t) => t.id), ['long', 'front'])
  const behindBoth = { id: 'back', foot: { x0: 1, x1: 2, y0: -2, y1: -1 }, bounds: { x: 0, y: 0, width: 200, height: 100 } }
  assert.deepEqual(paintOrder([long, behindBoth, front]).map((t) => t.id), ['back', 'long', 'front'])
})

test('buildScene gives every node a solid, every connection a line, and leaves contained_in out', () => {
  const doc = clone()
  doc.edges = [{ from: 'n_api', to: 'n_db', type: 'reads' }, { from: 'n_api', to: 'g_vpc', type: 'contained_in' }]
  const scene = buildScene({ document: doc, types })
  assert.equal(scene.solids.length + scene.floats.length, 3)
  assert.equal(scene.zones.length, 1)
  assert.equal(scene.edges.length, 1)
  assert.equal(drawnEdges(doc).length, 1)
  assert.ok(scene.bounds.width > 0)
  const wide = buildScene({ document: doc, types, states: new Map([['n_api', 'finding']]) })
  assert.equal(wide.solids.find((s) => s.id === 'n_api').tone, 'c')
  assert.equal(scene.solids.find((s) => s.id === 'n_api').draft, false)
})

// ---------------------------------------------------------------- names, sense points, the draft footprint

import { labelWidth, placeLabels, rectHitsPolygon } from '../src/studio/scene/labels.js'
import { statesFor } from '../src/studio/scene/sensepoints.js'

const VIEW = { x: 0, y: 0, zoom: 1 }
const PANE = { width: 800, height: 600 }

const solidAt = (id, x, y, width = 60, height = 50, kind = 'solid') => ({ id, kind, rect: { x, y, width, height }, polygon: [[x, y], [x + width, y], [x + width, y + height], [x, y + height]] })

test('two names that would overlap are never both shown, and the more important one stays', () => {
  const shown = placeLabels(VIEW, PANE, [
    { id: 'first', anchor: [100, 100], text: 'orders-api' },
    { id: 'second', anchor: [110, 104], text: 'orders-db' },
    { id: 'far', anchor: [500, 300], text: 'logs' },
  ])
  assert.ok(shown.has('first') && shown.has('far'))
  const rects = [...shown.entries()].map(([id, spot]) => {
    const anchor = { first: [100, 100], second: [110, 104], far: [500, 300] }[id]
    const w = labelWidth(id === 'first' ? 'orders-api' : id === 'second' ? 'orders-db' : 'logs')
    return { x: anchor[0] + spot.dx - w / 2, y: anchor[1] + spot.dy - 10, width: w, height: 20 }
  })
  for (let i = 0; i < rects.length; i += 1) for (let j = i + 1; j < rects.length; j += 1) assert.equal(rectsOverlap(rects[i], rects[j], 0), false)
})

test('a name never covers another solid, a person badge or a sense point: it moves, stands off with a leader line, or is left out', () => {
  const ownBox = { x: 80, y: 40, width: 40, height: 60 }
  const mine = { id: 'me', anchor: [100, 100], own: ownBox, text: 'orders-api' }
  const open = placeLabels(VIEW, PANE, [mine]).get('me')
  assert.equal(open.at, 'below')
  // A solid right under it: the name goes above.
  const above = placeLabels(VIEW, PANE, [mine], { obstacles: [solidAt('other', 30, 100, 150, 40)] }).get('me')
  assert.notEqual(above.at, 'below')
  assert.equal(above.at, 'above')
  // A person badge above as well: it goes beside, or stands off with a line.
  const beside = placeLabels(VIEW, PANE, [mine], { obstacles: [solidAt('other', 30, 100, 150, 40), solidAt('badge', 30, 0, 150, 38, 'float')] }).get('me')
  assert.ok(beside.at === 'left' || beside.at === 'right' || beside.leader)
  // Boxed in on every side: left out, never drawn over something.
  const boxed = placeLabels(VIEW, PANE, [mine], { obstacles: [solidAt('wall', -400, -400, 1600, 1500)] })
  assert.equal(boxed.has('me'), false)
  // A sense point (a rectangle with no outline) counts too.
  const ring = { id: 'sense-1', kind: 'sense', rect: { x: 40, y: 100, width: 130, height: 30 } }
  assert.notEqual(placeLabels(VIEW, PANE, [mine], { obstacles: [ring] }).get('me').at, 'below')
})

test('a name that stands off has a leader line that starts at its solid and ends at the name', () => {
  const mine = { id: 'me', anchor: [100, 100], own: { x: 80, y: 40, width: 40, height: 60 }, text: 'orders-api' }
  const crowd = [solidAt('under', 30, 100, 150, 25), solidAt('over', 30, 0, 150, 38), solidAt('left', -200, 0, 240, 200), solidAt('right', 130, 38, 300, 62)]
  const spot = placeLabels(VIEW, PANE, [mine], { obstacles: crowd }).get('me')
  assert.ok(spot, 'a free place exists a little way off')
  assert.equal(spot.leader, true)
  assert.equal(spot.from.length, 2)
  assert.equal(spot.to.length, 2)
})

test('the selected name is always shown, even when nothing is free, and the most important names are placed first', () => {
  const walls = [solidAt('wall', -400, -400, 1600, 1500)]
  const shown = placeLabels(VIEW, PANE, [{ id: 'picked', anchor: [100, 100], text: 'orders-api', must: true }, { id: 'other', anchor: [300, 300], text: 'x' }], { obstacles: walls })
  assert.deepEqual([...shown.keys()], ['picked'])
})

test('no names are shown when the picture is too small to read them against, except the selected one', () => {
  const small = { x: 0, y: 0, zoom: 0.2 }
  const shown = placeLabels(small, PANE, [{ id: 'a', anchor: [100, 100], text: 'a' }, { id: 'b', anchor: [400, 300], text: 'b', must: true }])
  assert.deepEqual([...shown.keys()], ['b'])
})

test('a zone name takes its corner, then slides along its back walls as far as they reach, and no further', () => {
  const corner = { id: 'z1', anchor: [200, 120], text: 'account-prod', zone: true, reach: { left: 0, right: 0 } }
  const second = { id: 'z2', anchor: [200, 120], text: 'prod-vpc', zone: true, reach: { left: 0, right: 0 } }
  assert.deepEqual([...placeLabels(VIEW, PANE, [corner, second]).keys()], ['z1'], 'a wall that does not reach leaves the second name out')
  const roomy = { ...second, reach: { left: 400, right: 400 } }
  const both = placeLabels(VIEW, PANE, [corner, roomy])
  assert.deepEqual([...both.keys()], ['z1', 'z2'])
  assert.notEqual(both.get('z2').dx, 0)
  // a person badge over its corner is never covered by a zone's name
  const badge = solidAt('badge', 150, 70, 100, 50, 'float')
  assert.equal(placeLabels(VIEW, PANE, [corner], { obstacles: [badge] }).has('z1'), false)
  // but a solid is not an obstacle to a zone's name
  assert.equal(placeLabels(VIEW, PANE, [corner], { obstacles: [solidAt('s', 150, 70, 100, 50)] }).has('z1'), true)
})

test('a rectangle meets a convex polygon only where they share ground', () => {
  const diamond = [[50, 0], [100, 25], [50, 50], [0, 25]]
  assert.equal(rectHitsPolygon({ x: 40, y: 20, width: 10, height: 10 }, diamond), true)
  assert.equal(rectHitsPolygon({ x: 0, y: 0, width: 20, height: 10 }, diamond), false, 'the bounding box corner is empty')
  assert.equal(rectHitsPolygon({ x: 120, y: 0, width: 20, height: 10 }, diamond), false)
  assert.equal(rectHitsPolygon({ x: 0, y: 0, width: 20, height: 10 }, diamond, 12), true, 'with air around it')
})

test('a node with an accepted sense point is watched, and a finding wins over it', () => {
  const states = statesFor([{ node: 'a', status: 'accepted' }, { node: 'b', status: 'suggested' }, { node: 'c', status: 'rejected' }], new Map([['a', ['open']], ['d', ['open']]]))
  assert.equal(states.get('a'), 'finding')
  assert.equal(states.get('b'), undefined)
  assert.equal(states.get('c'), undefined)
  assert.equal(states.get('d'), 'finding')
  assert.equal(statesFor([{ node: 'a', status: 'accepted' }]).get('a'), 'watched')
})

test('a Draft node stands on a dashed footprint, and a finished one does not', () => {
  assert.ok(solid('backend', { tone: 'w', draft: true }).markup.includes('draft-base'))
  assert.ok(!solid('backend', { tone: 'w', draft: false }).markup.includes('draft-base'))
  assert.ok(!solid('backend', { tone: 'w', draft: true, detail: false }).markup.includes('draft-base'))
})

test('a person is drawn with its own badge, and a sense point shows its status three different ways', async () => {
  const { sensePoint } = await import('../src/studio/scene/shapes.js')
  const accepted = sensePoint(0, 0, 'accepted')
  const suggested = sensePoint(0, 0, 'suggested')
  const rejected = sensePoint(0, 0, 'rejected')
  assert.ok(accepted.includes('class="sense accepted"') && suggested.includes('class="sense suggested"') && rejected.includes('class="sense rejected"'))
  assert.ok(rejected.includes('class="bar"') && !accepted.includes('class="bar"'))
  assert.ok(suggested.includes('pulse') && !accepted.includes('pulse'))
  for (const markup of [accepted, suggested, rejected]) assert.ok(markup.includes('class="robot'))
})

// ---------------------------------------------------------------- the ground, the busiest solids, people

import { packRects } from '../src/studio/packing.js'
import { UNIT_X as UX, UNIT_Y as UY } from '../src/studio/scene/project.js'

const sum = (box) => box.width / UX + box.height / UY
const manyNodes = (count, extra = {}) => Array.from({ length: count }, (_, i) => ({ id: `n_${i}`, type: 'service.backend@1', name: `svc-${String(i).padStart(3, '0')}`, environment: 'prod', ...extra }))

test('a small container is packed beside a large one, not under it', () => {
  const packed = packRects([{ id: 'big', width: 800, height: 600 }, { id: 'small', width: 200, height: 150 }], { gap: { x: 40, y: 64 }, aspect: 1.5 })
  const big = packed.at.get('big')
  const small = packed.at.get('small')
  const apart = small.x >= big.x + 800 || small.y >= big.y + 600
  assert.ok(apart, 'they do not overlap')
  assert.ok(packed.width <= 800 + 40 + 200 || packed.height <= 600 + 64 + 150)
})

test('no packed rectangle overlaps another, whatever the sizes', () => {
  const cells = Array.from({ length: 23 }, (_, i) => ({ id: `c${i}`, width: 160 + ((i * 37) % 5) * 90, height: 80 + ((i * 53) % 4) * 110 }))
  const packed = packRects(cells, { gap: { x: 40, y: 64 }, aspect: 1.5 })
  for (let i = 0; i < cells.length; i += 1) {
    for (let j = i + 1; j < cells.length; j += 1) {
      const a = { ...packed.at.get(cells[i].id), width: cells[i].width, height: cells[i].height }
      const b = { ...packed.at.get(cells[j].id), width: cells[j].width, height: cells[j].height }
      assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y, `${cells[i].id} and ${cells[j].id} overlap`)
    }
  }
})

test('a busy zone with no stored places lays out as a block about as wide as it is deep on the screen, not a column', () => {
  const doc = clearLayout(clone({ groups: [{ id: 'g_one', type: 'boundary.vpc@1', name: 'one', kind: 'vpc', environment: 'prod' }], nodes: manyNodes(40, { location: 'g_one' }), edges: [] }))
  const boxes = sceneBoxes(doc, types)
  const zone = boxes.get('g_one')
  const across = zone.width / UX
  const deep = zone.height / UY
  assert.ok(across / deep < 1.7 && deep / across < 1.7, `${across.toFixed(1)} by ${deep.toFixed(1)} units`)
  const rows = new Set([...boxes.entries()].filter(([id]) => id !== 'g_one').map(([, box]) => box.y))
  const columns = new Set([...boxes.entries()].filter(([id]) => id !== 'g_one').map(([, box]) => box.x))
  assert.ok(rows.size >= 4 && columns.size >= 4, 'several rows and several columns')
  assert.ok(sum(zone) < 22, `a zone of 40 nodes takes ${sum(zone).toFixed(1)} units of screen`)
})

test('what stands outside the platform forms compact blocks at two corners, not one long line', () => {
  const doc = clearLayout(clone({ groups: [{ id: 'g_in', type: 'boundary.vpc@1', name: 'in', kind: 'vpc', environment: 'prod' }],
    nodes: [...manyNodes(4, { location: 'g_in' }), ...Array.from({ length: 12 }, (_, i) => ({ id: `n_repo_${i}`, type: 'repo.github@1', name: `repo-${i}`, environment: 'prod' }))], edges: [] }))
  const withRepo = new Map([...types, ['repo.github@1', { id: 'repo.github', version: 1, family: 'code_and_delivery', display_name: 'GitHub', properties: [], edges: { allowed_out: [], allowed_in: [] } }]])
  const boxes = sceneBoxes(doc, withRepo)
  const platform = platformBox(doc, boxes, withRepo)
  const repos = [...boxes.entries()].filter(([id]) => id.startsWith('n_repo_')).map(([, box]) => box)
  assert.equal(new Set(repos.map((box) => box.x)).size >= 3 && new Set(repos.map((box) => box.y)).size >= 3, true)
  for (const box of repos) assert.ok(box.x >= platform.x + platform.width, 'on the right of the platform')
})

test('the busiest node is the deep teal tower and the next busiest stand taller, but only in a clone big enough to have a busiest', () => {
  const doc = clone({ groups: [], nodes: manyNodes(10), edges: [] })
  doc.edges = [1, 2, 3, 4, 5].map((i) => ({ from: 'n_0', to: `n_${i}`, type: 'calls' })).concat([{ from: 'n_6', to: 'n_7', type: 'calls' }, { from: 'n_6', to: 'n_8', type: 'calls' }])
  const scene = buildScene({ document: doc, types })
  const tower = scene.solids.find((s) => s.id === 'n_0')
  assert.equal(tower.isTower, true)
  assert.equal(tower.tone, 'd')
  assert.equal(tower.degree, 5)
  assert.equal(scene.solids.filter((s) => s.isTower).length, 1)
  const tall = (id) => scene.solids.find((s) => s.id === id).height
  assert.ok(tall('n_0') > tall('n_9') * 1.3, 'the tower stands highest')
  assert.ok(tall('n_6') > tall('n_9'), 'a busier node stands taller than an idle one')
  const small = buildScene({ document: clone({ groups: [], nodes: manyNodes(5), edges: [{ from: 'n_0', to: 'n_1', type: 'calls' }, { from: 'n_0', to: 'n_2', type: 'calls' }, { from: 'n_0', to: 'n_3', type: 'calls' }] }), types })
  assert.equal(small.solids.some((s) => s.isTower), false, 'five nodes have no tower')
})

test('a finding turns even the tower red, and a person is never the tower', () => {
  const doc = clone({ groups: [], nodes: [...manyNodes(9), { id: 'n_who', type: 'identity.okta@1', name: 'who', environment: 'prod' }], edges: [] })
  doc.edges = [1, 2, 3, 4].map((i) => ({ from: 'n_who', to: `n_${i}`, type: 'calls' })).concat([1, 2, 3].map((i) => ({ from: 'n_0', to: `n_${i + 4}`, type: 'calls' })))
  const scene = buildScene({ document: doc, types, states: new Map([['n_0', 'finding']]) })
  assert.equal(scene.floats.some((f) => f.isTower), false)
  assert.equal(scene.solids.find((s) => s.id === 'n_0').tone, 'c')
})

test('a person floats high enough that its badge covers no solid and no other badge', () => {
  // A tall solid stands two units behind each person on the ground, which is straight above it on the screen: at the lowest lift the badge would cover it.
  const doc = clone({ groups: [], nodes: [
    { id: 'n_behind_a', type: 'service.backend@1', name: 'behind a', environment: 'prod', layout: { x: 0, y: 0 } },
    { id: 'n_a', type: 'identity.okta@1', name: 'a', environment: 'prod', layout: { x: 320, y: 192 } },
    { id: 'n_behind_b', type: 'service.backend@1', name: 'behind b', environment: 'prod', layout: { x: 800, y: 0 } },
    { id: 'n_b', type: 'identity.okta@1', name: 'b', environment: 'prod', layout: { x: 1120, y: 192 } },
  ], edges: [] })
  const scene = buildScene({ document: doc, types })
  assert.equal(scene.floats.length, 2)
  assert.ok(scene.floats.every((f) => f.lift > 3.4), 'lifted above the lowest')
  for (const person of scene.floats) {
    const xs = person.badge.map((p) => p[0])
    const ys = person.badge.map((p) => p[1])
    const rect = { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) }
    for (const solidItem of scene.solids) assert.equal(rectHitsPolygon(rect, solidItem.outline), false, `${person.id} covers ${solidItem.id}`)
  }
  const low = buildScene({ document: clone({ groups: [], nodes: [{ id: 'n_a', type: 'identity.okta@1', name: 'a', environment: 'prod', layout: { x: 0, y: 0 } }], edges: [] }), types })
  assert.equal(low.floats[0].lift, 3.4, 'a person with nothing near it stays at the lowest lift')
})

test('every drawn thing has a place for its name, and a person badge is a circle, not the whole drop line', () => {
  const doc = clone()
  doc.nodes.push({ id: 'n_who', type: 'identity.okta@1', name: 'who', environment: 'prod', layout: { x: 900, y: 200 } })
  const scene = buildScene({ document: doc, types })
  for (const item of [...scene.solids, ...scene.floats]) assert.equal(item.nameAt.length, 2)
  const person = scene.floats[0]
  assert.equal(person.badge.length, 16)
  assert.ok(person.nameAt[1] > person.badge[0][1], 'the name hangs under the badge')
})

test('before the type library has loaded, no node is drawn red as if it had a finding', () => {
  const loading = buildScene({ document: clone(), types: new Map() })
  const drawn = [...loading.solids, ...loading.floats]
  assert.ok(drawn.length > 0)
  assert.deepEqual(drawn.filter((s) => s.tone === 'c').map((s) => s.id), [])
})
