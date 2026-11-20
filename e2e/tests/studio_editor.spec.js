/** Clone Studio as an editor (STUDIO.1): build by dragging, connect, move, resize, group, undo and redo, versions, keyboard and read-only. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { assertAccessible, attachGate, shoot } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/STUDIO.1')
// Its own server and database: the clones these journeys make must not become the clones other specs open.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.describe.configure({ mode: 'serial' })
// The journeys share one clone, so they run in this order.
const HAND = 'hand-built'
const KEYS = 'keys-only'

const node = (page, name) => page.locator('.react-flow__node-estate').filter({ has: page.getByText(name, { exact: true }) })
const zone = (page, name) => page.locator('.react-flow__node-boundary').filter({ has: page.getByText(name, { exact: true }) })
const library = (page) => page.getByRole('complementary', { name: 'Node library' })
// A node's drawn card carries its state as a class: Draft, Described, and while a connection is drawn its fit.
const card = (page, id, state) => page.locator(`[data-id="${id}"] .estate-node.${state}`)
const inspector = (page) => page.getByRole('complementary', { name: 'Inspector' })

// fill replaces the whole value in one step; the old select-all and type could land after the field had been drawn again, and append instead.
async function replaceField(page, selector, text) {
  const field = page.locator(selector)
  await expect(async () => {
    await field.fill(text)
    await expect(field).toHaveValue(text, { timeout: 1000 })
  }).toPass()
}

async function pick(page, trigger, name) {
  await page.locator(trigger).focus()
  await page.keyboard.press('Enter')
  const option = page.getByRole('option', { name })
  await expect(option).toBeVisible()
  await option.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('listbox')).toHaveCount(0)
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

// With one clone the bar names it; with several it is a picker.
async function openClone(page, baseURL, email, slug) {
  await signIn(page, baseURL, email)
  await page.goto(`${baseURL}/studio`)
  const picker = page.getByRole('combobox', { name: 'Clone' })
  await expect(picker.or(page.getByRole('toolbar', { name: 'Clone' }).getByText(slug, { exact: true })).first()).toBeVisible()
  if (await picker.isVisible()) {
    await picker.click()
    await page.getByRole('option', { name: slug }).click()
  }
  await expect(node(page, 'orders-api')).toBeVisible()
}

const item = (doc, id) => [...doc.nodes, ...doc.groups].find((entry) => entry.id === id)

async function startClone(page, app, slug) {
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio`)
  const blank = page.getByRole('button', { name: 'Start a blank clone' })
  const more = page.getByRole('button', { name: 'New clone' })
  await expect(blank.or(more)).toBeVisible()
  await (await blank.isVisible() ? blank : more).click()
  await replaceField(page, '#clone-name', slug)
  // The dialog refuses until the library has loaded, and says so; press again until the clone is made.
  await expect(async () => {
    await page.getByRole('button', { name: 'Create clone' }).click()
    await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible({ timeout: 1500 })
  }).toPass()
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
}

// A real pointer drag from the library onto the canvas, at a point inside the canvas.
async function dropType(page, type, x, y) {
  const canvas = await page.locator('.studio-canvas').boundingBox()
  await library(page).locator(`[data-type="${type}"]`).dragTo(page.locator('.studio-canvas'), { targetPosition: { x, y } })
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  return { x: canvas.x + x, y: canvas.y + y }
}

async function dragBy(page, locator, dx, dy, grab = { x: 60, y: 14 }) {
  const box = await locator.boundingBox()
  await page.mouse.move(box.x + grab.x, box.y + grab.y)
  await page.mouse.down()
  await page.mouse.move(box.x + grab.x + dx / 2, box.y + grab.y + dy / 2, { steps: 6 })
  await page.mouse.move(box.x + grab.x + dx, box.y + grab.y + dy, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
}

test('an engineer builds a small clone from empty with the mouse and it is all saved as versions', async ({ page, app }) => {
  test.setTimeout(240_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await startClone(page, app, HAND)

  // 1. Drag types from the library: a network group, then services and a database, each where it was dropped.
  const vpc = await dropType(page, 'boundary.vpc', 250, 300)
  await replaceField(page, '#item-name', 'shop-network')
  const orders = await dropType(page, 'service.backend', 170, 280)
  await replaceField(page, '#item-name', 'orders-api')
  await dropType(page, 'service.worker', 330, 380)
  await replaceField(page, '#item-name', 'payment-worker')
  await dropType(page, 'service.frontend', 150, 600)
  await replaceField(page, '#item-name', 'shop-web')
  await dropType(page, 'data.rds', 470, 120)
  await replaceField(page, '#item-name', 'orders-db')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  const first = await saved(page, HAND)
  expect(first.document.nodes).toHaveLength(4)
  expect(first.document.groups).toHaveLength(1)
  // Dropped inside the network group: it holds them. Dropped outside: it does not.
  expect(item(first.document, 'n_backend_1').location).toBe('g_vpc_1')
  expect(item(first.document, 'n_worker_1').location).toBe('g_vpc_1')
  expect(item(first.document, 'n_frontend_1').location).toBeUndefined()
  expect(item(first.document, 'n_rds_1').location).toBeUndefined()
  const ordersBox = await node(page, 'orders-api').boundingBox()
  expect(Math.abs(ordersBox.x + ordersBox.width / 2 - orders.x)).toBeLessThan(40)
  expect(vpc.x).toBeGreaterThan(0)
  await page.screenshot({ path: path.join(SHOTS, 'built-1440.png'), fullPage: true })

  // 2. Draw a connection by dragging from one node's dot onto the other node, then pick its kind.
  const dot = await page.locator('[data-id="n_backend_1"] .react-flow__handle.source').boundingBox()
  const target = await node(page, 'orders-db').boundingBox()
  await page.mouse.move(dot.x + dot.width / 2, dot.y + dot.height / 2)
  await page.mouse.down()
  await page.mouse.move(target.x + 90, target.y + 12, { steps: 12 })
  await expect(card(page, 'n_rds_1', 'link-typed')).toHaveCount(1)
  await page.screenshot({ path: path.join(SHOTS, 'connecting-1440.png'), fullPage: true })
  await page.mouse.up()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Connect orders-api to orders-db')
  await expect(dialog).toContainText('The library allows Reads, Writes, Depends on')
  await expect(page.locator('#edge-type')).toContainText('Reads')
  await page.getByRole('button', { name: 'Add connection' }).click()
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)

  // 3. Change the connection's kind in the side panel; a kind the library forbids is refused with its reason.
  await expect(inspector(page).getByRole('heading', { name: 'Reads' })).toBeVisible()
  await pick(page, '#edge-kind', 'Publishes to')
  await expect(inspector(page).getByRole('alert')).toContainText('cannot receive a Publishes to connection')
  await expect(inspector(page).getByRole('heading', { name: 'Reads' })).toBeVisible()
  await pick(page, '#edge-kind', 'Writes')
  await expect(inspector(page).getByRole('heading', { name: 'Writes' })).toBeVisible()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect((await saved(page, HAND)).document.edges.map((edge) => edge.type)).toEqual(['writes'])

  // 4. Move a node with the mouse: it keeps its new place after a reload.
  const before = await node(page, 'shop-web').boundingBox()
  await dragBy(page, node(page, 'shop-web'), 120, -60)
  const after = await node(page, 'shop-web').boundingBox()
  expect(Math.abs(after.x - before.x - 120)).toBeLessThan(12)
  expect(Math.abs(after.y - before.y + 60)).toBeLessThan(12)

  // 5. Drag a node into the group and out of it: the nesting follows.
  const net = await zone(page, 'shop-network').boundingBox()
  await dragBy(page, node(page, 'shop-web'), net.x + 80 - (after.x + 60), net.y + 110 - (after.y + 14))
  expect(item((await saved(page, HAND)).document, 'n_frontend_1').location).toBe('g_vpc_1')
  const inside = await node(page, 'shop-web').boundingBox()
  await dragBy(page, node(page, 'shop-web'), 0, 330)
  expect(item((await saved(page, HAND)).document, 'n_frontend_1').location).toBeUndefined()
  expect(inside.y).toBeGreaterThan(0)

  // 6. Resize the group with its corner: it grows, and keeps what it holds.
  await zone(page, 'shop-network').locator('.boundary-label').click()
  const corner = await zone(page, 'shop-network').locator('.react-flow__resize-control.handle.bottom.right').boundingBox()
  const wasWide = (await zone(page, 'shop-network').boundingBox()).width
  await page.mouse.move(corner.x + 5, corner.y + 5)
  await page.mouse.down()
  await page.mouse.move(corner.x + 45, corner.y + 45, { steps: 6 })
  await page.mouse.up()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect((await zone(page, 'shop-network').boundingBox()).width).toBeGreaterThan(wasWide + 20)

  // 7. Settings, owner and notes in the side panel.
  await node(page, 'orders-api').click()
  await replaceField(page, '#item-owner', 'Payments team')
  await replaceField(page, '#item-notes', 'Takes every checkout call.')
  await pick(page, '#prop-kind', 'api')
  await replaceField(page, '#prop-runtime', 'python 3.12')
  await pick(page, '#prop-exposure', 'internal')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  await expect(card(page, 'n_backend_1', 'described')).toHaveCount(1)

  // 8. Undo and redo, by button and by key.
  const count = (await saved(page, HAND)).versions.length
  await page.getByRole('button', { name: 'Undo last change' }).click()
  await expect(card(page, 'n_backend_1', 'draft')).toHaveCount(1)
  await page.getByRole('button', { name: 'Redo' }).click()
  await expect(card(page, 'n_backend_1', 'described')).toHaveCount(1)
  await page.locator('.react-flow__pane').click({ position: { x: 10, y: 10 } })
  await page.keyboard.press('ControlOrMeta+Z')
  await expect(card(page, 'n_backend_1', 'draft')).toHaveCount(1)
  await page.keyboard.press('ControlOrMeta+Shift+Z')
  await expect(card(page, 'n_backend_1', 'described')).toHaveCount(1)
  await expect(page.getByTestId('save-state')).toHaveText('Saved')

  // 9. Every save is a version with its author and a reason, and the history says so.
  const done = await saved(page, HAND)
  expect(done.versions.length).toBeGreaterThan(count)
  for (const version of done.versions) {
    expect(version.author).toBe('Kavya Iyer')
    expect(version.reason.length).toBeGreaterThan(3)
    expect(version.created_at).toBeTruthy()
  }
  const reasons = done.versions.map((version) => version.reason)
  expect(reasons).toContain('Placed Virtual network (VPC or VNet)')
  expect(reasons.some((reason) => reason.startsWith('Connected orders-api to orders-db'))).toBe(true)
  expect(reasons.some((reason) => reason.startsWith('Undid: '))).toBe(true)
  expect(reasons.some((reason) => reason.startsWith('Redid: '))).toBe(true)
  await page.getByRole('button', { name: 'Version history' }).click()
  const history = page.getByRole('dialog', { name: 'Version history' })
  await expect(history).toContainText('Kavya Iyer')
  await expect(history).toContainText('Placed Virtual network (VPC or VNet)')
  await page.keyboard.press('Escape')

  // 10. Reopen: every place, the nesting, the owner and the connection are still there.
  await page.reload()
  await expect(node(page, 'orders-api')).toBeVisible()
  const reopened = await saved(page, HAND)
  expect(item(reopened.document, 'n_backend_1').owner).toBe('Payments team')
  expect(item(reopened.document, 'n_backend_1').notes).toBe('Takes every checkout call.')
  expect(reopened.document.edges).toHaveLength(1)
  // The opening view fits the clone to the screen, so places are compared as saved, not as drawn.
  for (const entry of [...done.document.nodes, ...done.document.groups]) expect(item(reopened.document, entry.id).layout).toEqual(entry.layout)
  await page.getByRole('button', { name: 'Fit to screen' }).click()
  await page.keyboard.press('Escape')
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
  await assertAccessible(page)
  await page.screenshot({ path: path.join(SHOTS, 'reopened-1440.png'), fullPage: true })
  gate.assertClean()
})

test('many items are selected, lined up, grouped and put back with undo, with the mouse and the keys', async ({ page, app }) => {
  test.setTimeout(180_000)
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openClone(page, app.baseURL, 'engineer@acme.example', HAND)

  // Select two with the modifier key, and see the tools for many.
  await node(page, 'payment-worker').click()
  await node(page, 'orders-db').click({ modifiers: ['Shift'] })
  await expect(inspector(page)).toContainText('2 items selected')
  const first = await node(page, 'orders-db').boundingBox()
  await page.getByRole('button', { name: 'Align left edges' }).click()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  const lined = await node(page, 'orders-db').boundingBox()
  const worker = await node(page, 'payment-worker').boundingBox()
  expect(Math.abs(lined.x - worker.x)).toBeLessThan(1)
  expect(first.x).not.toBe(lined.x)
  // The arrow keys move them together, 16 px at a time.
  await node(page, 'orders-db').focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  const moved = await node(page, 'orders-db').boundingBox()
  expect(Math.round(moved.x - lined.x)).toBe(16)
  expect(Math.round((await node(page, 'payment-worker').boundingBox()).x - worker.x)).toBe(16)
  await page.keyboard.press('ControlOrMeta+Z')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect(Math.round((await node(page, 'orders-db').boundingBox()).x - lined.x)).toBe(0)

  // Select everything with the keys and group it in a new container; one undo takes the container away.
  await page.locator('.react-flow__pane').click({ position: { x: 10, y: 10 } })
  await page.keyboard.press('ControlOrMeta+A')
  await expect(inspector(page)).toContainText('items selected')
  await page.keyboard.press('ControlOrMeta+G')
  const dialog = page.getByRole('dialog', { name: 'items in a container' })
  await expect(dialog).toBeVisible()
  await pick(page, '#group-type', 'AWS account')
  await page.getByRole('button', { name: 'Group them' }).click()
  await expect(zone(page, 'AWS account')).toBeVisible()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  const grouped = await saved(page, HAND)
  expect(grouped.document.groups.map((group) => group.kind)).toContain('account')
  expect(item(grouped.document, 'g_vpc_1').parent).toBe('g_aws_account_1')
  await page.getByRole('button', { name: 'Undo last change' }).click()
  await expect(zone(page, 'AWS account')).toHaveCount(0)
  await expect(page.getByTestId('save-state')).toHaveText('Saved')

  // The container list never offers a group to itself: a group cannot go inside itself.
  // An empty corner of the container is clicked: a connection may cross its title.
  await zone(page, 'shop-network').click({ position: { x: 12, y: 150 } })
  await page.locator('#item-inside').focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('option', { name: 'Top level (in no container)' })).toBeVisible()
  await expect(page.getByRole('option', { name: 'shop-network' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('listbox')).toHaveCount(0)
  gate.assertClean()
})

test('someone who can only look sees why, cannot edit, and the clone stays as it was', async ({ page, app }) => {
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openClone(page, app.baseURL, 'ceo@acme.example', HAND)
  const banner = page.locator('.studio-readonly')
  await expect(banner).toContainText('Read only.')
  await expect(banner).toContainText('Chief Executive')
  await expect(banner).toContainText('Engineer or Project admin')
  await expect(page.getByRole('button', { name: 'Undo last change' })).toHaveCount(0)
  await expect(library(page).locator('[draggable="true"]')).toHaveCount(0)
  await expect(library(page)).not.toContainText('Drag a type onto the canvas')

  // A drag moves nothing and saves nothing; the dots to draw a connection are not offered.
  // (Dragging a node that cannot be moved pans the view, so the test compares two nodes with each other.)
  const gap = async () => {
    const [a, b] = [await node(page, 'orders-api').boundingBox(), await node(page, 'orders-db').boundingBox()]
    return [Math.round(b.x - a.x), Math.round(b.y - a.y)]
  }
  const before = await gap()
  const requests = []
  page.on('request', (request) => { if (request.method() !== 'GET') requests.push(request.url()) })
  await page.mouse.move(0, 0)
  const grab = await node(page, 'orders-api').boundingBox()
  await page.mouse.move(grab.x + 60, grab.y + 14)
  await page.mouse.down()
  await page.mouse.move(grab.x + 140, grab.y + 54, { steps: 8 })
  await page.mouse.up()
  expect(await gap()).toEqual(before)
  expect(requests).toEqual([])
  await node(page, 'orders-api').click()
  await expect(inspector(page)).toContainText('Payments team')
  await expect(inspector(page)).not.toContainText('Draw a connection')
  await expect(page.locator('#item-owner')).toHaveCount(0)
  await page.keyboard.press('Delete')
  await expect(node(page, 'orders-api')).toBeVisible()
  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Keyboard and mouse' })).toContainText('work for people who can edit')
  await page.keyboard.press('Escape')
  await page.screenshot({ path: path.join(SHOTS, 'read-only-1440.png'), fullPage: true })
  await assertAccessible(page)
  gate.assertClean()
})

test('a keyboard alone builds, connects, groups, undoes and saves', async ({ page, app }) => {
  test.setTimeout(180_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await startClone(page, app, KEYS)

  // Enter on a library type places it; the new item is selected and its name field takes F2.
  for (const [query, type, name] of [['backend service', 'Backend service or API', 'api'], ['aurora', 'Amazon Aurora PostgreSQL', 'ledger']]) {
    await replaceField(page, '[aria-label="Search the node library"]', query)
    await library(page).getByRole('button', { name: type }).focus()
    await page.keyboard.press('Enter')
    await expect(page.locator('#item-name')).toBeVisible()
    await page.keyboard.press('F2')
    await expect(page.locator('#item-name')).toBeFocused()
    await page.keyboard.press('ControlOrMeta+A')
    await page.keyboard.insertText(name)
    await expect(node(page, name)).toBeVisible()
  }
  // A container by key: a type picked in the library goes in as a boundary.
  await replaceField(page, '[aria-label="Search the node library"]', 'VPC')
  await library(page).getByRole('button', { name: 'Virtual network' }).focus()
  await page.keyboard.press('Enter')
  await expect(zone(page, 'Virtual network (VPC or VNet)')).toBeVisible()
  await page.keyboard.press('F2')
  await page.keyboard.press('ControlOrMeta+A')
  await page.keyboard.insertText('network')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')

  // Put the service inside the container from the side panel, without the mouse.
  await node(page, 'api').focus()
  await page.keyboard.press('Enter')
  await pick(page, '#item-inside', 'network')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect(item((await saved(page, KEYS)).document, 'n_backend_1').location).toBe('g_vpc_1')

  // C draws a connection from the selected node; the dialog is all keys.
  await node(page, 'api').focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('c')
  await pick(page, '#edge-target', 'ledger')
  await expect(page.locator('#edge-type')).toContainText('Reads')
  await page.getByRole('button', { name: 'Add connection' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)

  // Arrow keys move, Alt and arrows resize the container, Ctrl+Z and Ctrl+Shift+Z undo and redo, Delete removes.
  await node(page, 'ledger').focus()
  await page.keyboard.press('Enter')
  const start = await node(page, 'ledger').boundingBox()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Shift+ArrowRight')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  const moved = await node(page, 'ledger').boundingBox()
  expect(Math.round(moved.x - start.x)).toBe(64)
  expect(Math.round(moved.y - start.y)).toBe(16)
  await page.keyboard.press('ControlOrMeta+Z')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect(Math.round((await node(page, 'ledger').boundingBox()).x - start.x)).toBe(0)
  await page.keyboard.press('ControlOrMeta+Shift+Z')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect(Math.round((await node(page, 'ledger').boundingBox()).x - start.x)).toBe(64)
  await zone(page, 'network').focus()
  await page.keyboard.press('Enter')
  const width = (await zone(page, 'network').boundingBox()).width
  await page.keyboard.press('Alt+ArrowRight')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  expect((await zone(page, 'network').boundingBox()).width).toBeGreaterThan(width + 10)
  await node(page, 'ledger').focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Delete')
  await expect(node(page, 'ledger')).toHaveCount(0)
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).focus()
  await page.keyboard.press('Enter')
  await expect(node(page, 'ledger')).toBeVisible()
  await expect(page.getByTestId('save-state')).toHaveText('Saved')

  // The list of keys is one question mark away.
  await page.locator('body').press('Escape')
  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Keyboard and mouse' })).toContainText('Ctrl+G')
  await page.keyboard.press('Escape')
  gate.assertClean()
})

test('the editor passes the five-width gate with a built clone open', async ({ page, app }) => {
  test.setTimeout(120_000)
  const gate = attachGate(page)
  await openClone(page, app.baseURL, 'engineer@acme.example', HAND)
  await page.locator('.react-flow__pane').click({ position: { x: 5, y: 5 } })
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
  await shoot(page, 'editor', { mask: [page.locator('.studio-version'), page.locator('.studio-clone')] })
  await page.setViewportSize({ width: 1440, height: 900 })
  await assertAccessible(page)
  gate.assertClean()
})
