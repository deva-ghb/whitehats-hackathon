/**
 * P2.5: the proving-ground estate drawn by hand in the Studio, through the screen only, inside the Fintech shop project.
 *
 * An engineer drags every type from the library onto the plan view, fills the side panel (name, owner, notes and the
 * required properties) and draws the connections, then exports the clone and checks it against the schema and the node library.
 * The run is recorded on video. The plan is in proving-ground-plan.js; it is written from the repository, not from the Terraform state.
 */

import { execFile } from 'node:child_process'
import { copyFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { assertAccessible, attachGate } from '../gate.js'
import { CLONE, COLUMN, EDGES, GROUPS, NODES, ROW } from '../proving-ground-plan.js'
import { signIn } from '../sign-in.js'

const run = promisify(execFile)
const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = path.resolve(HERE, '../screenshots/P2.5')
const BACKEND = path.resolve(HERE, '../../backend')
const CHECKER = path.resolve(HERE, '../check_clone.py')

// Its own server and database: the clone this builds must not become one that other specs open. Everything is recorded on video.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.use({ video: { mode: 'on', size: { width: 1280, height: 720 } }, viewport: { width: 2000, height: 1150 } })
test.describe.configure({ mode: 'serial' })

const only = process.env.PLAN_ONLY ? Number(process.env.PLAN_ONLY) : Infinity  // a short run while the plan is being written

const library = (page) => page.getByRole('complementary', { name: 'Node library' })
const inspector = (page) => page.getByRole('complementary', { name: 'Inspector' })
const saveState = (page) => page.getByTestId('save-state')
const canvasOf = (page) => page.locator('.studio-canvas')

// A drawn item, found by the words a screen reader hears: its type, its name and a comma ("Serverless function shop-api, Described").
const itemNamed = (page, type, name) => page.getByRole('group', { name: `${type} ${name},` })

async function settle(page) {
  await page.waitForTimeout(650)  // typing is saved a moment after the last key
  await expect(saveState(page)).toHaveText('Saved')
}

async function pickOption(page, trigger, wanted) {
  await trigger.click()
  await page.getByRole('option').filter({ has: page.getByText(wanted, { exact: true }) }).first().click()
  await expect(page.getByRole('listbox')).toHaveCount(0)
}

// The view: where a ground point is on the screen. The drawing surface says so in its own transform.
async function screenOf(page, ground) {
  const [shift, scale] = await page.locator('.react-flow__viewport').evaluate((view) => {
    const text = view.style.transform
    return [text.split('translate(')[1].split(')')[0].split(',').map((part) => parseFloat(part)), parseFloat(text.split('scale(')[1])]
  })
  const frame = await canvasOf(page).boundingBox()
  return { x: frame.x + shift[0] + ground.x * scale, y: frame.y + shift[1] + ground.y * scale, scale }
}

// A real pointer drag from the library onto the canvas: the drop lands under the pointer.
async function dropType(page, typeName, point) {
  const frame = await canvasOf(page).boundingBox()
  await library(page).getByRole('button', { name: typeName }).first().dragTo(canvasOf(page), { targetPosition: { x: point.x - frame.x, y: point.y - frame.y } })
  await expect(saveState(page)).toHaveText('Saved')
}

async function fillSettings(page, entry) {
  const panel = inspector(page)
  await panel.getByLabel('Name', { exact: true }).fill(entry.name)
  await panel.getByLabel('Owner', { exact: true }).fill(entry.owner)
  await panel.getByLabel('Notes', { exact: true }).fill(entry.notes)
  for (const [label, widget, value] of entry.props) {
    if (widget === 'text' || widget === 'list') await panel.getByLabel(label, { exact: true }).fill(value)
    else await pickOption(page, panel.getByRole('combobox', { name: label, exact: true }), value)
  }
  await settle(page)
}

async function resizeTo(page, zone, size) {
  await zone.locator('.boundary-label').click()
  const handle = zone.locator('.react-flow__resize-control.handle.bottom.right')
  await expect(handle).toBeVisible()
  const now = await zone.boundingBox()
  const corner = await handle.boundingBox()
  const { scale } = await screenOf(page, { x: 0, y: 0 })
  const from = { x: corner.x + corner.width / 2, y: corner.y + corner.height / 2 }
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  await page.mouse.move(from.x + (size.width * scale - now.width) / 2, from.y + (size.height * scale - now.height) / 2, { steps: 8 })
  await page.mouse.move(from.x + (size.width * scale - now.width), from.y + (size.height * scale - now.height), { steps: 8 })
  await page.mouse.up()
  await expect(saveState(page)).toHaveText('Saved')
}

async function saved(page) {
  return page.evaluate(async (name) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}` }
    const list = await (await fetch(`/api/clones?project=${project}`, { headers })).json()
    const found = list.clones.find((clone) => clone.clone === name)
    if (!found) return null
    const document = (await (await fetch(`/api/clones/${found.id}?project=${project}`, { headers })).json()).document
    return { id: found.id, document }
  }, CLONE)
}

const typeName = (entry) => entry.type
const zoneOf = (page, entry) => itemNamed(page, entry.type, entry.name)

test('an engineer draws the whole proving-ground estate by hand, every node Described, and exports it', async ({ page, app }, info) => {
  test.setTimeout(1_500_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)

  // 1. Sign in as an engineer who may edit the Fintech shop project, and look at the clones that are already there.
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio`)
  const blank = page.getByRole('button', { name: 'Start a blank clone' })
  const more = page.getByRole('button', { name: 'New clone' })
  await expect(blank.or(more)).toBeVisible()
  const picker = page.getByRole('combobox', { name: 'Clone' })
  let existing = []
  if (await picker.isVisible()) {
    await picker.click()
    existing = await page.getByRole('option').allInnerTexts()
    await page.keyboard.press('Escape')
  } else if (!(await blank.isVisible())) {
    existing = [await page.getByRole('toolbar', { name: 'Clone' }).locator('b').first().innerText()]
  }
  info.annotations.push({ type: 'clones already in the project', description: existing.length ? existing.join(' | ') : 'none' })
  expect(existing.join(' ')).not.toContain(CLONE)

  // 2. A new blank clone with a name of its own.
  await (await blank.isVisible() ? blank : more).click()
  await page.getByLabel('Clone name').fill(CLONE)
  await expect(async () => {
    await page.getByRole('button', { name: 'Create clone' }).click()
    await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible({ timeout: 1500 })
  }).toPass()
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
  // The plan (flat) view: the isometric scene is the other choice and has its own tests.
  await page.getByRole('button', { name: 'Plan view' }).click()
  await expect(page.getByRole('button', { name: 'Plan view' })).toHaveAttribute('aria-pressed', 'true')

  // 3. Zoom the plan out until the whole estate fits, and move it to the left.
  for (let press = 0; press < 3; press += 1) await page.getByRole('button', { name: 'Zoom out' }).click()
  await page.mouse.move(700, 600)
  await page.mouse.down({ button: 'middle' })
  await page.mouse.move(450, 600, { steps: 10 })
  await page.mouse.up({ button: 'middle' })

  // 4. The two containers: the account, then the region inside it. Each is dropped small and stretched by its corner.
  const [account, region] = GROUPS
  const at = async (ground) => screenOf(page, ground)
  const dropGroup = async (entry, centre) => {
    await dropType(page, entry.type, await at(centre))
    await fillSettings(page, entry)
    await resizeTo(page, zoneOf(page, entry), entry.size)
  }
  await dropGroup(account, { x: account.at.x + 196, y: account.at.y + 128 })
  await dropGroup(region, { x: region.at.x + 196, y: region.at.y + 128 })
  await expect(page.getByRole('heading', { name: region.name })).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'containers-2000.png') })

  // 5. Every node, dragged from the library to its place: inside the region, or on the open ground outside the account.
  const regionBox = await zoneOf(page, region).boundingBox()
  const { scale } = await at({ x: 0, y: 0 })
  const nodes = NODES.slice(0, only)
  for (const entry of nodes) {
    const centre = entry.inside
      ? { x: regionBox.x + (40 + COLUMN * entry.col + 116) * scale, y: regionBox.y + (60 + ROW * entry.row + 36) * scale }
      : await at({ x: entry.open.x + 116, y: entry.open.y + 36 })
    await dropType(page, typeName(entry), centre)
    await fillSettings(page, entry)
    await expect(itemNamed(page, entry.type, entry.name), await inspector(page).innerText()).toContainText('Described')
  }
  await page.screenshot({ path: path.join(SHOTS, 'nodes-2000.png') })

  // 6. Connections. The first few are drawn with the pointer, from one node's dot onto the other node; the rest from the side panel.
  const byKey = new Map(NODES.map((entry) => [entry.key, entry]))
  const edges = EDGES.filter(([from, , to]) => byKey.has(from) && byKey.has(to) && NODES.indexOf(byKey.get(from)) < only && NODES.indexOf(byKey.get(to)) < only)
  const POINTER_DRAWN = 4
  for (const [index, [from, kind, to]] of edges.entries()) {
    const source = byKey.get(from)
    const target = byKey.get(to)
    const dialog = page.getByRole('dialog')
    if (index < POINTER_DRAWN) {
      const dot = await zoneOf(page, source).locator('.react-flow__handle.source').boundingBox()
      const over = await zoneOf(page, target).boundingBox()
      await page.mouse.move(dot.x + dot.width / 2, dot.y + dot.height / 2)
      await page.mouse.down()
      await page.mouse.move(over.x + over.width / 2, over.y + over.height / 2, { steps: 14 })
      await page.mouse.up()
      await expect(dialog).toContainText(`Connect ${source.name} to ${target.name}`)
    } else {
      await zoneOf(page, source).click()
      await inspector(page).getByRole('button', { name: 'Draw a connection' }).click()
      await pickOption(page, dialog.getByRole('combobox', { name: 'Connect to' }), target.name)
    }
    await pickOption(page, dialog.getByRole('combobox', { name: 'Connection type' }), kind)
    await expect(dialog.getByRole('alert')).toHaveCount(0)
    await dialog.getByRole('button', { name: 'Add connection' }).click()
    await expect(dialog).toHaveCount(0)
    await expect(inspector(page).getByRole('heading', { name: kind })).toBeVisible()
    await settle(page)
  }
  await page.getByRole('button', { name: 'Fit to screen' }).click()

  // 7. What was saved: every node and container Described, owner and notes on each, every connection one of the library's kinds.
  const made = await saved(page)
  expect(made.document.nodes).toHaveLength(nodes.length)
  expect(made.document.groups).toHaveLength(GROUPS.length)
  expect(made.document.edges).toHaveLength(edges.length)
  for (const item of [...made.document.nodes, ...made.document.groups]) {
    expect(item.owner, `${item.name} has an owner`).toBeTruthy()
    expect(item.notes, `${item.name} has notes`).toBeTruthy()
    expect(item.provenance).toEqual({ kind: 'drawn', source: 'Clone Studio' })
  }
  await expect(page.locator('.estate-node.draft')).toHaveCount(0)
  await expect(page.locator('.boundary-node.draft')).toHaveCount(0)
  await expect(page.getByText('Problems').locator('..').getByText('None')).toBeVisible()
  await assertAccessible(page)

  // 8. Closed and opened again, it is all still there; then the five widths, looked at by a person after the run.
  await page.reload()
  await expect(itemNamed(page, 'Serverless function', 'shop-api')).toContainText('Described')
  await expect(page.locator('.estate-node.described')).toHaveCount(nodes.length)
  await expect(page.locator('.react-flow__edge')).toHaveCount(edges.length)
  for (const width of [390, 768, 1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.getByRole('button', { name: 'Fit to screen' }).click()
    await page.waitForTimeout(400)
    if (width === 390) expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1)
    await page.screenshot({ path: path.join(SHOTS, `hand-built-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 2000, height: 1150 })

  // 9. Export the clone, the way a person does, and check the file against the schema and the node library.
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export' }).click()
  const file = await (await download).path()
  const exported = path.join(SHOTS, `${CLONE}.clone.json`)
  await copyFile(file, exported)
  const checked = await run('uv', ['run', 'python', CHECKER, exported], { cwd: BACKEND })
  console.log(checked.stdout)
  const report = JSON.parse(checked.stdout)
  expect(report.problems).toEqual([])
  expect(report.not_described).toEqual([])
  expect(report.described).toBe(nodes.length + GROUPS.length)
  gate.assertClean()
})
