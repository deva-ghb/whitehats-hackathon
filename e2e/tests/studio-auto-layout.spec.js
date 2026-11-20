/** A clone with no stored places is laid out by its own structure (STUDIO.5): grouped, a sensible shape, every name readable when it opens. */

import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { WIDTHS, assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = path.resolve(HERE, '../screenshots/STUDIO.5')
const TWIN = path.resolve(HERE, '../screenshots/P2.5/proving-ground-by-hand.clone.json')
const SLUG = 'twin-without-places'

// Its own server and database: the clone this journey makes must not become the clone other specs open.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

/** The imported twin's shape (28 nodes in two containers) as an import leaves it: no places at all. */
async function twinWithoutPlaces() {
  const document = JSON.parse(await readFile(TWIN, 'utf8'))
  const stripped = {
    ...document, clone: SLUG,
    nodes: document.nodes.map(({ layout, ...node }) => node),
    groups: document.groups.map(({ layout, ...group }) => group),
  }
  // The exported clone names the proving ground's vault; the demo company's own is the one the server accepts.
  return JSON.parse(JSON.stringify(stripped).replaceAll('vault://tenant/proving-ground/', 'vault://tenant/acme/'))
}

async function saveClone(page, document) {
  const status = await page.evaluate(async (body) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const response = await fetch(`/api/clones?project=${project}`, {
      method: 'POST', body: JSON.stringify(body), headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    })
    return response.status
  }, { reason: 'Layout check', document })
  expect(status).toBe(200)
}

async function openStudio(page, app, view) {
  await page.evaluate((choice) => localStorage.setItem('wh-studio-view', choice), view)
  await page.goto(`${app.baseURL}/studio`)
  const toolbar = page.getByRole('toolbar', { name: 'Clone' })
  await expect(toolbar).toBeVisible()
  const picker = page.getByRole('combobox', { name: 'Clone' })
  if (await picker.isVisible()) {
    await picker.click()
    await page.getByRole('option', { name: SLUG }).first().click()
  }
  await expect(toolbar.getByText(SLUG, { exact: true })).toBeVisible()
}

test.describe.configure({ mode: 'serial' })

test('the plan opens as a grouped picture that fits the screen, with every name on a card readable', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example', 'plan')
  const document = await twinWithoutPlaces()
  await saveClone(page, document)
  await openStudio(page, app, 'plan')

  const cards = page.locator('.react-flow__node-estate')
  await expect(cards).toHaveCount(document.nodes.length)
  const canvas = await page.locator('.react-flow').boundingBox()

  // The whole clone is on screen as it opens: not one tall column cut off at the edge.
  const boxes = []
  for (const card of await cards.all()) {
    const box = await card.boundingBox()
    expect(box.x, 'card starts inside the canvas').toBeGreaterThanOrEqual(canvas.x - 1)
    expect(box.y, 'card starts inside the canvas').toBeGreaterThanOrEqual(canvas.y - 1)
    expect(box.x + box.width, 'card ends inside the canvas').toBeLessThanOrEqual(canvas.x + canvas.width + 1)
    expect(box.y + box.height, 'card ends inside the canvas').toBeLessThanOrEqual(canvas.y + canvas.height + 1)
    boxes.push(box)
  }
  expect(new Set(boxes.map((box) => Math.round(box.x))).size, 'more than one column').toBeGreaterThan(3)

  // Each card is big enough to read its name, and the name is there.
  for (const card of await cards.all()) {
    const name = card.locator('.estate-copy b')
    await expect(name).toBeVisible()
    const text = await name.evaluate((el) => ({ size: el.getBoundingClientRect().height, written: el.textContent }))
    expect(text.written.length).toBeGreaterThan(0)
    expect(text.size, `"${text.written}" is readable`).toBeGreaterThanOrEqual(10)
  }

  // The two containers are drawn around their children, with their names.
  await expect(page.locator('.react-flow__node-boundary')).toHaveCount(document.groups.length)

  // Opening a clone does not save places for it: it stays one version with no stored layout.
  const saved = await page.evaluate(async (name) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}` }
    const list = await (await fetch(`/api/clones?project=${project}`, { headers })).json()
    const found = list.clones.find((clone) => clone.clone === name)
    const detail = await (await fetch(`/api/clones/${found.id}?project=${project}`, { headers })).json()
    return { versions: (await (await fetch(`/api/clones/${found.id}/versions?project=${project}`, { headers })).json()).versions.length,
      placed: detail.document.nodes.filter((node) => node.layout).length }
  }, SLUG)
  expect(saved).toEqual({ versions: 1, placed: 0 })

  await assertAccessible(page)
  await mkdir(SHOTS, { recursive: true })
  for (const width of WIDTHS) {
    // A person opens the Studio at their own width, so each shot is a fresh opening, not the 1440 fit squeezed.
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await openStudio(page, app, 'plan')
    await expect(cards).toHaveCount(document.nodes.length)
    await page.waitForTimeout(800)
    // The watch panel and the view switch float over the canvas: the fitted picture keeps clear of both.
    const covers = await page.locator('.sense-panel, .studio-view').evaluateAll((overlays) => {
      const hits = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
      const shown = overlays.map((el) => el.getBoundingClientRect())
      return [...document.querySelectorAll('.react-flow__node-estate')].filter((card) => shown.some((box) => hits(box, card.getBoundingClientRect()))).length
    })
    expect(covers, `cards under the watch panel or the view switch at ${width}`).toBe(0)
    // A bar that loads after the fit (the problems summary) can move the canvas's edge by a few pixels; the Fit button fits again.
    const outside = await page.evaluate((SLACK) => {
      const frame = document.querySelector('.studio-canvas').getBoundingClientRect()
      return [...document.querySelectorAll('.react-flow__node-estate')].map((card) => card.getBoundingClientRect())
        .filter((box) => box.left < frame.left - SLACK || box.top < frame.top - SLACK || box.right > frame.right + SLACK || box.bottom > frame.bottom + SLACK)
        .map((box) => [Math.round(box.top), Math.round(box.bottom), Math.round(frame.top), Math.round(frame.bottom)])
    }, 48)
    expect(outside, `cards outside the canvas at ${width}: [card top, card bottom, canvas top, canvas bottom]`).toEqual([])
    // The canvas is never squeezed to a strip by the library and the inspector beside it.
    const across = (await page.locator('.studio-canvas').boundingBox()).width
    if (width >= 1024) expect(across, `canvas width at ${width}`).toBeGreaterThanOrEqual(500)
    await page.screenshot({ path: path.join(SHOTS, `auto-layout-plan-${width}.png`) })
  }
  gate.assertClean()
})

test('the scene opens the same clone as one estate with its solids and labels, nothing outside the platform but what belongs outside', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example', 'plan')
  await openStudio(page, app, 'scene')
  const document = await twinWithoutPlaces()
  await expect(page.locator('[data-scene-item]')).toHaveCount(document.nodes.length + document.groups.length)
  await assertAccessible(page)
  await mkdir(SHOTS, { recursive: true })
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await openStudio(page, app, 'scene')
    await expect(page.locator('[data-scene-item]')).toHaveCount(document.nodes.length + document.groups.length)
    await page.waitForTimeout(800)
    await page.screenshot({ path: path.join(SHOTS, `auto-layout-scene-${width}.png`) })
  }
  gate.assertClean()
})
