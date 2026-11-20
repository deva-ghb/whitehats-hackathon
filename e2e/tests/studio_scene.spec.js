/** The Studio as the website's isometric scene (STUDIO.2): build by dragging on the ground, connect, sense points (stored: P6.3), undo, versions, plan view. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { WIDTHS, assertAccessible, attachGate, shoot } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/STUDIO.3')
// Its own server and database: the clones these journeys make must not become the clones other specs open.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.describe.configure({ mode: 'serial' })
const SCENE = 'scene-built'

const library = (page) => page.getByRole('complementary', { name: 'Node library' })
const inspector = (page) => page.getByRole('complementary', { name: 'Inspector' })
const solid = (page, id) => page.locator(`[data-scene-item][data-id="${id}"]`)
const edges = (page) => page.locator('[data-scene-edge]')
const sceneView = (page) => page.getByRole('button', { name: 'Scene view' })
const planView = (page) => page.getByRole('button', { name: 'Plan view' })

async function replaceField(page, selector, text) {
  await page.locator(selector).focus()
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.insertText(text)
}

// The signed-in person's own saved copy of a clone, read the way the app reads it.
async function saved(page, slug) {
  return page.evaluate(async (name) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}` }
    const list = await (await fetch(`/api/clones?project=${project}`, { headers })).json()
    const found = list.clones.find((clone) => clone.clone === name)
    if (!found) return null
    const document = (await (await fetch(`/api/clones/${found.id}?project=${project}`, { headers })).json()).document
    const versions = (await (await fetch(`/api/clones/${found.id}/versions?project=${project}`, { headers })).json()).versions
    return { id: found.id, document, versions }
  }, slug)
}

async function startClone(page, app, slug) {
  await signIn(page, app.baseURL, 'engineer@acme.example', 'scene')
  await page.goto(`${app.baseURL}/studio`)
  const blank = page.getByRole('button', { name: 'Start a blank clone' })
  const more = page.getByRole('button', { name: 'New clone' })
  await expect(blank.or(more)).toBeVisible()
  await (await blank.isVisible() ? blank : more).click()
  await replaceField(page, '#clone-name', slug)
  await expect(async () => {
    await page.getByRole('button', { name: 'Create clone' }).click()
    await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible({ timeout: 1500 })
  }).toPass()
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
}

// A real pointer drag from the library onto the ground, at a screen point.
async function dropType(page, type, at) {
  const canvas = await page.locator('.studio-canvas').boundingBox()
  await library(page).locator(`[data-type="${type}"]`).dragTo(page.locator('.studio-canvas'), { targetPosition: { x: at.x - canvas.x, y: at.y - canvas.y } })
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
}

/**
 * A point on the zone's floor, given along its two ground axes (0 to 1 across x, 0 to 1 across y): worked out from the corners of the
 * floor as drawn, so it is on the floor whatever the zone's shape and the view's zoom are.
 */
async function onFloor(page, zoneId, u = 0.5, v = 0.5) {
  return solid(page, zoneId).locator('.zone-floor').evaluate((polygon, [along, across]) => {
    const to = (i) => {
      const point = polygon.points.getItem(i)
      const out = new DOMPoint(point.x, point.y).matrixTransform(polygon.getScreenCTM())
      return [out.x, out.y]
    }
    const [a, b, , d] = [0, 1, 2, 3].map(to)
    return { x: a[0] + along * (b[0] - a[0]) + across * (d[0] - a[0]), y: a[1] + along * (b[1] - a[1]) + across * (d[1] - a[1]) }
  }, [u, v])
}

const centre = async (locator) => {
  const box = await locator.boundingBox()
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

// Select a solid by its own focus and Enter. A person badge floats over whatever stands behind it and a tall solid covers the one
// behind it, so no fixed point on the screen is always its own; the pointer's own hit test is covered by the unit tests.
async function press(page, id) {
  await solid(page, id).focus()
  await page.keyboard.press('Enter')
}

// With several clones the bar is a picker; open the one a journey built.
async function pickClone(page, slug) {
  const picker = page.getByRole('combobox', { name: 'Clone' })
  await expect(picker.or(page.getByRole('toolbar', { name: 'Clone' }).getByText(slug, { exact: true })).first()).toBeVisible()
  if (await picker.isVisible()) {
    await picker.click()
    await page.getByRole('option', { name: slug }).click()
  }
}

// The "Where to watch" panel starts closed so it never covers the canvas.
async function openSensePanel(page) {
  const bar = page.getByRole('region', { name: 'Where to watch' }).locator('.sense-head')
  await expect(async () => {
    if ((await bar.getAttribute('aria-expanded')) !== 'true') await bar.click()
    await expect(bar).toHaveAttribute('aria-expanded', 'true', { timeout: 1500 })
  }).toPass()
}

async function rename(page, name) {
  await replaceField(page, '#item-name', name)
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
}

test('the Studio opens the scene on a wide screen and the plan on a phone, and remembers a choice', async ({ page, app }) => {
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example', null)
  await page.goto(`${app.baseURL}/studio`)
  await expect(page.getByRole('button', { name: 'Start a blank clone' }).or(page.getByRole('button', { name: 'New clone' }))).toBeVisible()
  await page.getByRole('button', { name: 'Start a blank clone' }).click()
  await replaceField(page, '#clone-name', 'defaults-check')
  await expect(async () => {
    await page.getByRole('button', { name: 'Create clone' }).click()
    await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible({ timeout: 1500 })
  }).toPass()
  await expect(sceneView(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(planView(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(page.locator('.sc-svg')).toBeVisible()
  await planView(page).click()
  await expect(page.locator('.react-flow')).toBeVisible()
  await expect(page.locator('.sc-svg')).toHaveCount(0)
  await page.reload()
  await expect(planView(page)).toHaveAttribute('aria-pressed', 'true')
  // A phone gets the plan until a choice is made; a made choice wins.
  await page.evaluate(() => window.localStorage.removeItem('wh-studio-view'))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.reload()
  await expect(planView(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.react-flow')).toBeVisible()
  await sceneView(page).click()
  await expect(page.locator('.sc-svg')).toBeVisible()
  gate.assertClean()
})

test('an engineer builds a small clone by dragging on the ground of the scene', async ({ page, app }) => {
  test.setTimeout(240_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await startClone(page, app, SCENE)
  await expect(page.locator('.sc-svg')).toBeVisible()

  // 1. A zone first, then three services, a database and an identity dropped on its floor: each lands where it was dropped.
  const canvas = await page.locator('.studio-canvas').boundingBox()
  await dropType(page, 'boundary.vpc', { x: canvas.x + canvas.width / 2, y: canvas.y + canvas.height / 2 + 40 })
  await rename(page, 'shop-network')
  const zone = 'g_vpc_1'
  // A new zone is small (392 by 256 on the plan); stretch it by its corner so five solids have a floor to stand on.
  await solid(page, zone).focus()
  await page.keyboard.press('Enter')
  const corner0 = await centre(page.locator('.sc-handle:not(.connect)').nth(2))
  await page.mouse.move(corner0.x, corner0.y)
  await page.mouse.down()
  await page.mouse.move(corner0.x + 120, corner0.y + 40, { steps: 8 })
  await page.mouse.move(corner0.x + 240, corner0.y + 80, { steps: 8 })
  await page.mouse.up()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  await dropType(page, 'service.backend', await onFloor(page, zone, 0.2, 0.25))
  await rename(page, 'orders-api')
  await dropType(page, 'service.worker', await onFloor(page, zone, 0.75, 0.25))
  await rename(page, 'payment-worker')
  await dropType(page, 'service.frontend', await onFloor(page, zone, 0.2, 0.75))
  await rename(page, 'shop-web')
  await dropType(page, 'data.rds', await onFloor(page, zone, 0.78, 0.7))
  await rename(page, 'orders-db')
  await dropType(page, 'identity.okta', await onFloor(page, zone, 0.5, 0.5))
  await rename(page, 'staff-sign-in')
  const built = await saved(page, SCENE)
  expect(built.document.nodes).toHaveLength(5)
  expect(built.document.groups).toHaveLength(1)
  for (const entry of built.document.nodes) expect(entry.location, `${entry.name} sits in the zone it was dropped on`).toBe(zone)
  await expect(page.locator('.sc-solid')).toHaveCount(5)
  await expect(page.locator('.sc-label')).not.toHaveCount(0)
  await page.screenshot({ path: path.join(SHOTS, 'built-1440.png') })

  // 2. A connection, drawn by dragging from the handle of the selected solid onto another, then its kind.
  await press(page, 'n_backend_1')
  const handle = page.locator('.sc-handle.connect')
  await expect(handle).toBeVisible()
  const from = await centre(handle)
  const to = await centre(solid(page, 'n_rds_1'))
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 8 })
  await page.mouse.move(to.x, to.y + 4, { steps: 8 })
  await expect(page.locator('.sc-link-typed').first()).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'connecting-1440.png') })
  await page.mouse.up()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Connect orders-api to orders-db')
  await expect(dialog).toContainText('The library allows Reads, Writes, Depends on')
  await page.getByRole('button', { name: 'Add connection' }).click()
  await expect(edges(page)).toHaveCount(1)
  await expect(inspector(page).getByRole('heading', { name: 'Reads' })).toBeVisible()
  // A second connection by the keyboard: C on the selected solid asks where it goes.
  await press(page, 'n_worker_1')
  await page.keyboard.press('c')
  await page.getByRole('dialog').getByRole('combobox', { name: 'Connect to' }).click()
  await page.getByRole('option', { name: 'orders-db' }).click()
  await page.getByRole('button', { name: 'Add connection' }).click()
  await expect(edges(page)).toHaveCount(2)
  await expect(page.getByTestId('save-state')).toHaveText('Saved')

  // 3. A sense point placed by hand from the "Where to watch" panel: stored, accepted at once, drawn as a ring with the robot beside it.
  await openSensePanel(page)
  await press(page, 'n_backend_1')
  await page.getByRole('button', { name: 'Place a sense point on orders-api' }).click()
  await expect(page.locator('.sense.accepted .ring')).toHaveCount(1)
  await expect(page.locator('.sense.accepted .dot')).toHaveCount(1)
  await expect(page.locator('.sense.accepted .robot')).toHaveCount(1)
  await page.screenshot({ path: path.join(SHOTS, 'sense-point-1440.png') })

  // 4. Move a solid along the ground: it keeps its new place, and the zone still holds what it holds.
  const before = (await saved(page, SCENE)).document.nodes.find((n) => n.id === 'n_frontend_1').layout
  const web = await centre(solid(page, 'n_frontend_1'))
  await page.mouse.move(web.x, web.y + 4)
  await page.mouse.down()
  await page.mouse.move(web.x + 30, web.y + 8, { steps: 6 })
  await page.mouse.move(web.x + 60, web.y + 12, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  const after = (await saved(page, SCENE)).document.nodes.find((n) => n.id === 'n_frontend_1')
  expect(after.layout.x !== before.x || after.layout.y !== before.y).toBe(true)

  // 5. Resize the zone from its corner handle: it grows.
  await page.keyboard.press('Escape')
  await solid(page, zone).focus()
  await page.keyboard.press('Enter')
  const wasWide = (await saved(page, SCENE)).document.groups[0].layout.width
  const corner = page.locator('.sc-handle:not(.connect)').nth(2)
  await expect(corner).toBeVisible()
  const cornerAt = await centre(corner)
  await page.mouse.move(cornerAt.x, cornerAt.y)
  await page.mouse.down()
  await page.mouse.move(cornerAt.x + 40, cornerAt.y + 10, { steps: 6 })
  await page.mouse.move(cornerAt.x + 80, cornerAt.y + 20, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect((await saved(page, SCENE)).document.groups[0].layout.width).toBeGreaterThan(wasWide)

  // 6. Undo and redo, by button and by key: the connections go and come back.
  const count = (await saved(page, SCENE)).versions.length
  await page.keyboard.press('Escape')
  // The last two changes were the move and the resize; undoing them keeps both connections, the third undo takes one away.
  for (let step = 0; step < 2; step += 1) await page.getByRole('button', { name: 'Undo last change' }).click()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  await expect(edges(page)).toHaveCount(2)
  await page.getByRole('button', { name: 'Undo last change' }).click()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  await expect(edges(page)).toHaveCount(1)
  await page.getByRole('button', { name: 'Redo' }).click()
  await expect(edges(page)).toHaveCount(2)
  await page.keyboard.press('ControlOrMeta+Z')
  await expect(edges(page)).toHaveCount(1)
  await page.keyboard.press('ControlOrMeta+Shift+Z')
  await expect(edges(page)).toHaveCount(2)
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  await expect(page.locator('.sense.accepted')).toHaveCount(1)

  // 7. Every save is a version with its author and a reason; the clone reopens as it was.
  const done = await saved(page, SCENE)
  expect(done.versions.length).toBeGreaterThan(count)
  for (const version of done.versions) {
    expect(version.author).toBe('Kavya Iyer')
    expect(version.reason.length).toBeGreaterThan(3)
  }
  await page.getByRole('button', { name: 'Version history' }).click()
  await expect(page.getByRole('dialog', { name: 'Version history' })).toContainText('Placed Virtual network (VPC or VNet)')
  await page.keyboard.press('Escape')
  await page.reload()
  await pickClone(page, SCENE)
  await expect(page.locator('.sc-solid')).toHaveCount(5)
  await expect(edges(page)).toHaveCount(2)
  const reopened = await saved(page, SCENE)
  for (const entry of [...done.document.nodes, ...done.document.groups]) {
    expect([...reopened.document.nodes, ...reopened.document.groups].find((x) => x.id === entry.id).layout).toEqual(entry.layout)
  }
  await page.getByRole('button', { name: 'Fit the scene to the screen' }).click()
  await page.keyboard.press('Escape')
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
  await assertAccessible(page)
  await page.screenshot({ path: path.join(SHOTS, 'reopened-1440.png') })
  gate.assertClean()
})

test('the plan view does the same job: the clone built in the scene is there, and every name reads', async ({ page, app }) => {
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example', 'scene')
  await page.goto(`${app.baseURL}/studio`)
  await pickClone(page, SCENE)
  await expect(page.locator('.sc-svg')).toBeVisible()
  await planView(page).click()
  await expect(page.locator('.react-flow__node-estate')).toHaveCount(5)
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await expect(page.getByText('orders-api', { exact: true })).toBeVisible()
  await sceneView(page).click()
  await expect(page.locator('.sc-solid')).toHaveCount(5)
  gate.assertClean()
})

// The plan view is the default for a person who has only used the keyboard so far, and it does the same job without a mouse.
const KEYS = 'scene-keys'
const planNode = (page, name) => page.locator('.react-flow__node-estate').filter({ has: page.getByText(name, { exact: true }) })

async function pick(page, trigger, name) {
  await page.locator(trigger).focus()
  await page.keyboard.press('Enter')
  const option = page.getByRole('option', { name })
  await expect(option).toBeVisible()
  await option.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('listbox')).toHaveCount(0)
}

test('a first Tab, before any pointer, opens the plan view', async ({ page, app }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example', null)
  await page.goto(`${app.baseURL}/studio`)
  await expect(page.getByRole('toolbar', { name: 'Clone' })).toBeVisible()
  await expect(sceneView(page)).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Tab')
  await expect(planView(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.react-flow')).toBeVisible()
})

test('a keyboard alone does the same job in the plan view: zone, three services, a database, an identity, a connection, undo, a saved version', async ({ page, app }) => {
  test.setTimeout(180_000)
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example', 'plan')
  await page.goto(`${app.baseURL}/studio`)
  const blank = page.getByRole('button', { name: 'Start a blank clone' })
  const more = page.getByRole('button', { name: 'New clone' })
  await expect(blank.or(more)).toBeVisible()
  await (await blank.isVisible() ? blank : more).click()
  await replaceField(page, '#clone-name', KEYS)
  await expect(async () => {
    await page.getByRole('button', { name: 'Create clone' }).click()
    await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible({ timeout: 1500 })
  }).toPass()
  // An empty clone was open already (the first journey made one): wait until the new one is the one on screen.
  await expect(page.getByRole('toolbar', { name: 'Clone' })).toContainText(KEYS)
  await expect(planView(page)).toHaveAttribute('aria-pressed', 'true')

  // F2 moves to the Name field, select-all and type. Under load the field can be drawn again between the keys and the text is
  // appended to the old name, so the value is checked and the keys repeated until it is exactly the name.
  const rename_by_keys = async (name) => {
    await expect(async () => {
      await page.keyboard.press('F2')
      await page.keyboard.press('ControlOrMeta+A')
      await page.keyboard.insertText(name)
      await expect(page.locator('#item-name')).toHaveValue(name, { timeout: 1500 })
    }).toPass()
    await expect(page.getByTestId('save-state')).toHaveText('Saved')
  }
  const place = async (query, type, name) => {
    await replaceField(page, '[aria-label="Search the node library"]', query)
    await library(page).getByRole('button', { name: type }).focus()
    await page.keyboard.press('Enter')
    await rename_by_keys(name)
  }
  await replaceField(page, '[aria-label="Search the node library"]', 'VPC')
  await library(page).getByRole('button', { name: 'Virtual network' }).focus()
  await page.keyboard.press('Enter')
  await rename_by_keys('shop-network')
  await place('backend service', 'Backend service or API', 'orders-api')
  await place('worker', 'Worker or back-office service', 'payment-worker')
  await place('frontend', 'Frontend web app', 'shop-web')
  await place('aurora', 'Amazon Aurora PostgreSQL', 'orders-db')
  await place('okta', 'Okta Okta', 'staff-sign-in')
  for (const name of ['orders-api', 'payment-worker', 'shop-web', 'orders-db', 'staff-sign-in']) {
    await planNode(page, name).focus()
    await page.keyboard.press('Enter')
    await pick(page, '#item-inside', 'shop-network')
    await expect(page.getByTestId('save-state')).toHaveText('Saved')
  }
  const built = await saved(page, KEYS)
  expect(built.document.nodes).toHaveLength(5)
  for (const entry of built.document.nodes) expect(entry.location).toBe('g_vpc_1')

  await planNode(page, 'orders-api').focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('c')
  await pick(page, '#edge-target', 'orders-db')
  await expect(page.locator('#edge-type')).toContainText('Reads')
  await page.getByRole('button', { name: 'Add connection' }).focus()
  await expect(page.getByRole('button', { name: 'Add connection' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  await page.keyboard.press('ControlOrMeta+Z')
  await expect(page.locator('.react-flow__edge')).toHaveCount(0)
  await page.keyboard.press('ControlOrMeta+Shift+Z')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  const done = await saved(page, KEYS)
  expect(done.versions.length).toBeGreaterThan(8)
  expect(done.document.edges).toHaveLength(1)
  // The same clone, opened in the scene by the keyboard switch, shows the same things.
  await planView(page).focus()
  await sceneView(page).focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.sc-solid')).toHaveCount(5)
  await expect(edges(page)).toHaveCount(1)
  await assertAccessible(page)
  gate.assertClean()
})

test('the scene passes the five-width gate, in the light and in the dark, with names that never overlap', async ({ page, app }) => {
  test.setTimeout(180_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example', 'scene')
  await page.goto(`${app.baseURL}/studio`)
  await pickClone(page, SCENE)
  await expect(page.locator('.sc-solid')).toHaveCount(5)
  // One sense point of each kind, so the picture shows accepted, suggested and rejected side by side. The clone is told what is exposed
  // and what matters (the orders API reachable from outside and calling the worker, the database a crown jewel), so the analysis has paths to suggest from.
  await page.evaluate(async (slug) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const list = await (await fetch(`/api/clones?project=${project}`, { headers })).json()
    const id = list.clones.find((clone) => clone.clone === slug).id
    const document = (await (await fetch(`/api/clones/${id}?project=${project}`, { headers })).json()).document
    for (const node of document.nodes) {
      if (node.id === 'n_backend_1') node.properties = { ...node.properties, exposure: 'public' }
      if (node.id === 'n_rds_1') node.criticality = 'crown-jewel'
    }
    document.edges.push({ from: 'n_backend_1', to: 'n_worker_1', type: 'calls' })
    const made = await fetch(`/api/clones/${id}/versions?project=${project}`, { method: 'POST', headers, body: JSON.stringify({ reason: 'Marked what is exposed and what matters', document }) })
    return { status: made.status, body: await made.text() }
  }, SCENE).then((made) => expect(made.status, made.body).toBe(200))
  await page.reload()
  await pickClone(page, SCENE)
  await expect(page.locator('.sc-solid')).toHaveCount(5)
  await openSensePanel(page)
  await page.getByRole('button', { name: 'Run again' }).click()
  await expect(page.getByTestId('sense-counts')).toHaveText('1 accepted, 2 suggested, 0 rejected')
  await page.getByRole('button', { name: 'Reject the sense point on payment-worker' }).click()
  await press(page, 'n_frontend_1')
  await page.getByRole('button', { name: 'Place a sense point on shop-web' }).click()
  await expect(page.getByTestId('sense-counts')).toHaveText('2 accepted, 1 suggested, 1 rejected')
  // Closed, the panel leaves the picture alone.
  await page.getByRole('button', { name: 'Where to watch', exact: false }).first().click()
  await page.keyboard.press('Escape')
  await page.evaluate(() => document.activeElement?.blur())  // no focus ring on the panel's bar in the snapshots
  await page.mouse.move(5, 5)
  await expect(page.locator('.sense.accepted')).toHaveCount(2)
  await expect(page.locator('.sense.suggested')).toHaveCount(1)
  await expect(page.locator('.sense.rejected')).toHaveCount(1)
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
  const mask = [page.locator('.studio-version'), page.locator('.studio-clone')]
  // The picture's own place on the canvas depends on when the last resize settled (a slow machine refits a moment later), so the whole-page
  // snapshot allows 4 percent; what the picture must show is asserted below and in the screenshots next to the reference.
  await shoot(page, 'scene', { mask, maxDiffPixelRatio: 0.04 })
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.waitForTimeout(400)
    // Every name on the scene is at least 11 px on screen, and no two names overlap.
    const names = await page.evaluate(() => [...document.querySelectorAll('.sc-label text:not(.sub)')].map((el) => {
      const r = el.getBoundingClientRect()
      return { size: parseFloat(getComputedStyle(el).fontSize) * (el.getScreenCTM()?.a || 1), x: r.x, y: r.y, w: r.width, h: r.height }
    }))
    for (const name of names) expect(name.size).toBeGreaterThanOrEqual(11)
    for (let i = 0; i < names.length; i += 1) {
      for (let j = i + 1; j < names.length; j += 1) {
        const a = names[i]
        const b = names[j]
        const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y
        expect(apart, `names overlap at ${width} px`).toBe(true)
      }
    }
    await page.screenshot({ path: path.join(SHOTS, `scene-${width}.png`) })
    await page.locator('.studio-canvas').screenshot({ path: path.join(SHOTS, `scene-canvas-${width}.png`) })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await assertAccessible(page)
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(SHOTS, 'scene-dark-1440.png') })
  await assertAccessible(page)
  gate.assertClean()
})
