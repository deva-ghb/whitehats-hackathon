/** P6.3: where to watch. The analysis runs on the hand-built proving ground, a person accepts the top five, rejects one and places one
 *  by hand, and the coverage figure follows every step. Drawn in the scene (ring plus robot) and listed in both views. */

import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { assertAccessible, attachGate, shoot } from '../gate.js'
import { signIn } from '../sign-in.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = path.resolve(HERE, '../screenshots/P6.3')
const BY_HAND = path.resolve(HERE, '../screenshots/P2.5/proving-ground-by-hand.clone.json')
const SLUG = 'proving-ground-where-to-watch'
// Its own server and database: the clone this journey makes must not become the clone other specs open.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.describe.configure({ mode: 'serial' })

// What a person tells WhiteHats matters in the shop: the two tables, the receipts bucket and the two secrets.
const JEWELS = ['customers', 'orders', 'receipts', 'webhook-signing-secret', 'api-token']

const planView = (page) => page.getByRole('button', { name: 'Plan view' })
const sceneView = (page) => page.getByRole('button', { name: 'Scene view' })
const panel = (page) => page.getByRole('region', { name: 'Where to watch' })
const planNode = (page, name) => page.locator('.react-flow__node-estate').filter({ has: page.getByText(name, { exact: true }) })
const figure = async (page) => Number((await page.getByTestId('sense-coverage').innerText()).replace('%', ''))
// The bar that opens and closes the panel (its name also holds the figure, and the panel has a button named for finding where to watch).
const head = (page) => panel(page).locator('.sense-head')
const items = (page) => panel(page).getByRole('list', { name: 'Sense points' }).getByRole('listitem')

async function pick(page, trigger, name) {
  await page.locator(trigger).focus()
  await page.keyboard.press('Enter')
  const option = page.getByRole('option', { name })
  await expect(option).toBeVisible()
  await option.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('listbox')).toHaveCount(0)
}

/** The hand-built clone, exported by the P2.5 journey, stored through the API under a new name; the person marks what matters. */
async function openHandBuilt(page, app, view) {
  await signIn(page, app.baseURL, 'engineer@acme.example', view)
  const document = JSON.parse(await readFile(BY_HAND, 'utf8'))
  document.clone = SLUG
  const id = await page.evaluate(async (body) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const response = await fetch(`/api/clones?project=${project}`, { method: 'POST', body: JSON.stringify(body),
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } })
    return (await response.json()).id
  }, { reason: 'The hand-built proving ground, for where to watch', document })
  await page.goto(`${app.baseURL}/studio?clone=${id}`)
  return id
}

// The panel starts closed so it never covers the canvas; a person opens it, and the choice is remembered.
async function openPanel(page) {
  const bar = head(page)
  // The toolbar above can change height while the clone name arrives, so a click is repeated until the panel is open.
  await expect(async () => {
    if ((await bar.getAttribute('aria-expanded')) !== 'true') await bar.click()
    await expect(bar).toHaveAttribute('aria-expanded', 'true', { timeout: 1500 })
  }).toPass()
}

async function leftOnPage(page) {
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
}

test('the analysis runs on the hand-built proving ground: accept the top five, reject one, place one by hand, and the coverage follows', async ({ page, app }) => {
  test.setTimeout(240_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openHandBuilt(page, app, 'plan')
  await expect(planNode(page, 'shop-api')).toBeVisible({ timeout: 30_000 })

  // 1. Before anything is marked, there is nothing to attack: the panel says what to do, and the figure says so.
  await expect(panel(page)).toBeVisible()
  await expect(head(page)).toHaveAttribute('aria-expanded', 'false')
  await openPanel(page)
  await expect(page.getByTestId('sense-coverage')).toHaveText('No paths')
  await expect(panel(page)).toContainText('Mark what matters most')

  // 2. The person marks the crown jewels in the side panel (Criticality), each a saved version.
  for (const name of JEWELS) {
    await planNode(page, name).focus()
    await page.keyboard.press('Enter')
    await pick(page, '#item-criticality', 'Crown jewel')
    await leftOnPage(page)
  }
  // "Saved" can show for an instant before a change is sent, so wait for the version the five changes make: 1 and one for each.
  await expect(page.getByRole('toolbar', { name: 'Clone' })).toContainText(`Version ${1 + JEWELS.length},`)
  await leftOnPage(page)
  await page.keyboard.press('Escape')

  // 3. Find where to watch: stored suggestions with reasons, best first, no model needed. Nothing accepted, so the figure is 0.
  await panel(page).getByRole('button', { name: 'Find where to watch' }).click()
  await expect(items(page).first()).toBeVisible()
  const total = await items(page).count()
  expect(total).toBeGreaterThanOrEqual(7)
  await expect(page.getByTestId('sense-coverage')).toHaveText('0%')
  await expect(page.getByTestId('sense-coverage-words')).toContainText('of 41 attack paths')
  await expect(items(page).first()).toContainText('data key')
  await expect(items(page).first()).toContainText('is on 28 of 41 attack paths and has no logging or control')
  await expect(page.getByTestId('sense-counts')).toHaveText(`0 accepted, ${total} suggested, 0 rejected`)
  await page.screenshot({ path: path.join(SHOTS, 'suggested-plan-1440.png') })

  // 4. Accept the top five. The figure rises with the first and never falls.
  const readings = [await figure(page)]
  for (let rank = 1; rank <= 5; rank += 1) {
    await items(page).nth(rank - 1).getByRole('button', { name: 'Accept the sense point on' }).click()
    await expect(page.getByTestId('sense-counts')).toContainText(`${rank} accepted`)
    readings.push(await figure(page))
  }
  expect(readings[1], `the data key sits on most paths: ${readings}`).toBeGreaterThan(60)
  expect(readings).toEqual([...readings].sort((a, b) => a - b))
  expect(readings.at(-1)).toBeGreaterThan(readings[0])
  const afterFive = readings.at(-1)
  await expect(page.getByTestId('sense-coverage-words')).toContainText('attack paths have an accepted sense point on them')

  // 5. Reject the sixth: it is marked, and the figure does not move.
  await items(page).nth(5).getByRole('button', { name: 'Reject the sense point on' }).click()
  await expect(items(page).nth(5)).toHaveAttribute('data-status', 'rejected')
  await expect(page.getByTestId('sense-counts')).toContainText('1 rejected')
  expect(await figure(page)).toBe(afterFive)

  // 6. Place one by hand on a node the analysis did not suggest (the worker already logs): accepted at once.
  await planNode(page, 'payments-worker').focus()
  await page.keyboard.press('Enter')
  await panel(page).getByRole('button', { name: 'Place a sense point on payments-worker' }).click()
  await expect(page.getByTestId('sense-counts')).toContainText('6 accepted')
  await expect(panel(page).locator('li[data-node="n_function_3"]')).toContainText('Placed by hand')
  expect(await figure(page)).toBeGreaterThanOrEqual(afterFive)
  const finalFigure = await figure(page)
  const counts = await page.getByTestId('sense-counts').innerText()

  // 7. The plan view shows the same data on the cards, in words.
  await page.keyboard.press('Escape')
  await expect(planNode(page, 'data key')).toContainText('Watched')
  await expect(page.getByRole('group', { name: 'data key' }).filter({ hasText: 'Watched' })).toHaveCount(1)
  await page.screenshot({ path: path.join(SHOTS, 'decided-plan-1440.png') })

  // 8. The scene draws the same stored points: ring plus robot, accepted solid, suggested hollow, rejected struck through.
  await sceneView(page).click()
  // Coming from the plan, the scene opens fitted: the whole platform is on the canvas, not a zoomed corner of it.
  const canvas = await page.locator('.sc-svg').boundingBox()
  await expect(async () => {
    const slab = await page.locator('.sc-platform').boundingBox()
    expect(slab.x).toBeGreaterThanOrEqual(canvas.x - 1)
    expect(slab.y).toBeGreaterThanOrEqual(canvas.y - 1)
    expect(slab.x + slab.width).toBeLessThanOrEqual(canvas.x + canvas.width + 1)
    expect(slab.y + slab.height).toBeLessThanOrEqual(canvas.y + canvas.height + 1)
  }).toPass()
  await expect(page.locator('.sense.accepted')).toHaveCount(6)
  await expect(page.locator('.sense.rejected')).toHaveCount(1)
  await expect(page.locator('.sense.suggested')).toHaveCount(total - 6)
  await expect(page.locator('.sense.accepted .robot.happy')).toHaveCount(6)
  await expect(page.locator('.sense.rejected .robot.sad')).toHaveCount(1)
  await expect(page.locator('.sc-solid[aria-label*="sense point, accepted"]').first()).toBeAttached()
  await page.mouse.move(5, 5)
  await page.screenshot({ path: path.join(SHOTS, 'decided-scene-panel-1440.png') })
  await assertAccessible(page)
  // Closed, the panel is one bar with the figure and the picture is all there is to look at.
  await head(page).click()
  await expect(panel(page).getByRole('list', { name: 'Sense points' })).toHaveCount(0)
  await expect(page.getByTestId('sense-coverage')).toBeVisible()
  await page.mouse.move(5, 5)
  await page.screenshot({ path: path.join(SHOTS, 'decided-scene-1440.png') })
  await page.locator('.studio-canvas').screenshot({ path: path.join(SHOTS, 'decided-scene-canvas-1440.png') })
  await head(page).click()

  // 9. It is kept: a reload shows the same decisions and the same figure.
  await page.reload()
  await expect(items(page).first()).toBeVisible()
  await expect(page.getByTestId('sense-counts')).toHaveText(counts)
  expect(await figure(page)).toBe(finalFigure)
  await expect(page.locator('.sense.accepted')).toHaveCount(6)

  // 10. A change to the clone makes the points older than the drawing, says so, and a new analysis keeps every decision.
  await planView(page).click()
  await planNode(page, 'orders').focus()
  await page.keyboard.press('Enter')
  await page.locator('#item-notes').fill('Orders are the shop\'s crown jewel.')
  await leftOnPage(page)
  await expect(panel(page)).toContainText('Run it again to catch up: your decisions are kept.')
  await expect(panel(page).getByRole('button', { name: 'Place a sense point' })).toHaveCount(0)
  await panel(page).getByRole('button', { name: 'Run again' }).click()
  await expect(panel(page)).not.toContainText('Run it again to catch up')
  await expect(page.getByTestId('sense-counts')).toContainText('6 accepted, ')
  await expect(page.getByTestId('sense-counts')).toContainText('1 rejected')
  expect(await figure(page)).toBe(finalFigure)
  gate.assertClean()
})

test('the screen passes the five-width gate with the panel open and a person reads it with the keyboard alone', async ({ page, app }) => {
  test.setTimeout(240_000)
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example', 'plan')
  await page.goto(`${app.baseURL}/studio`)
  // The first journey's clone is the only one on this server, so it is the one the Studio opens.
  await expect(page.getByRole('toolbar', { name: 'Clone' })).toContainText(SLUG)
  await openPanel(page)
  await expect(items(page).first()).toBeVisible()

  // Keyboard only: Tab to the panel's own buttons, Enter to put a rejected suggestion back and to accept it.
  const reject = items(page).nth(5)
  await expect(reject).toHaveAttribute('data-status', 'rejected')
  await reject.getByRole('button', { name: 'back to suggested' }).focus()
  await page.keyboard.press('Enter')
  await expect(reject).toHaveAttribute('data-status', 'suggested')
  await reject.getByRole('button', { name: 'Accept the sense point on' }).focus()
  await page.keyboard.press('Enter')
  await expect(reject).toHaveAttribute('data-status', 'accepted')
  await assertAccessible(page)

  await page.mouse.move(5, 5)
  for (const width of [390, 768, 1024, 1440, 1920]) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await sceneView(page).click()
    await expect(page.locator('.sense.accepted').first()).toBeVisible()
    await page.screenshot({ path: path.join(SHOTS, `scene-${width}.png`) })
  }
  // The same picture at the widest size with the panel closed, for the comparison with the website's hero.
  await page.setViewportSize({ width: 1920, height: 1080 })
  await head(page).click()
  await expect(head(page)).toHaveAttribute('aria-expanded', 'false')
  await page.mouse.move(5, 5)
  await page.locator('.studio-canvas').screenshot({ path: path.join(SHOTS, 'scene-canvas-1920.png') })
  await page.setViewportSize({ width: 1440, height: 900 })
  gate.assertClean()
})
