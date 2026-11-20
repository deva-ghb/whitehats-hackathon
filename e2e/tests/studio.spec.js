/** Clone Studio: the five areas, import, layout, selection and the narrow layouts (P2.1). */

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, shoot, tabTo } from '../gate.js'
import { signIn } from '../sign-in.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = path.resolve(HERE, '../screenshots/P2.1')
const EXAMPLE = path.resolve(HERE, '../../../library/examples/proving-ground.clone.json')

// The example names its own company in vault paths; the demo company imports it as its own.
async function exampleFile(dir, name, edit = (text) => text) {
  const text = (await readFile(EXAMPLE, 'utf8')).replaceAll('vault://tenant/proving-ground/', 'vault://tenant/acme/')
  await mkdir(dir, { recursive: true })
  const file = path.join(dir, name)
  await writeFile(file, edit(text))
  return file
}

// Each test stands alone: the proving-ground clone is imported once, by an engineer, if it is not there yet.
async function withClone(page, app, info) {
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio`)
  const empty = page.getByRole('heading', { name: 'No clone yet' })
  await expect(empty.or(page.locator('.estate-node').first())).toBeVisible()
  if (await empty.isVisible()) {
    await page.getByTestId('clone-file').setInputFiles(await exampleFile(info.outputDir, 'proving-ground.clone.json'))
    await expect(page.locator('.estate-node').first()).toBeVisible()
  }
}

// Exact name, so "shop-api" does not also match "shop-api role".
const node = (page, name) => page.locator('.react-flow__node-estate').filter({ has: page.getByText(name, { exact: true }) })

// Notices fade after a few seconds; screenshots wait so they never land in a baseline.
async function quiet(page) {
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
}

// Every piece of text drawn on the canvas, as tall as it really is on screen (font size times zoom).
async function canvasTextSizes(page) {
  return page.evaluate(() => {
    const transform = document.querySelector('.react-flow__viewport').style.transform
    const zoom = parseFloat(transform.split('scale(')[1])
    return [...document.querySelectorAll('.react-flow__node b, .react-flow__node span')]
      .filter((el) => el.offsetParent && el.textContent.trim() && !el.children.length)
      .map((el) => parseFloat(getComputedStyle(el).fontSize) * zoom)
  })
}

async function assertReadable(page) {
  const sizes = await canvasTextSizes(page)
  for (const size of sizes) expect(size).toBeGreaterThanOrEqual(11)
}

const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name), fullPage: true })

test('an engineer imports a clone and the studio lays it out in five areas', async ({ page, app }, info) => {
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await tabTo(page, 'Clone Studio')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Clone Studio' })).toBeVisible()

  // Empty state says what to do next.
  await expect(page.getByRole('heading', { name: 'No clone yet' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Import a clone' })).toBeVisible()
  await assertAccessible(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await shot(page, 'empty-1440.png')

  // A file that is not JSON is refused with a reason, and nothing is created.
  await page.getByTestId('clone-file').setInputFiles(await exampleFile(info.outputDir, 'broken.json', () => '{ not json'))
  await expect(page.getByText('That file is not a clone document: it is not valid JSON.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No clone yet' })).toBeVisible()

  await page.getByTestId('clone-file').setInputFiles(await exampleFile(info.outputDir, 'proving-ground.clone.json'))
  await expect(page.getByText('Clone imported')).toBeVisible()
  await expect(page.locator('.estate-node')).toHaveCount(24)
  await expect(page.locator('.boundary-node')).toHaveCount(2)
  // It opens readable: names show, and no text on the canvas is under 11 px.
  await expect(page.locator('.estate-node.icons')).toHaveCount(0)
  expect((await canvasTextSizes(page)).length).toBeGreaterThan(0)
  await assertReadable(page)

  // The five areas.
  await expect(page.getByRole('toolbar', { name: 'Clone' })).toContainText('proving-ground')
  await expect(page.getByRole('complementary', { name: 'Node library' })).toBeVisible()
  await expect(page.locator('.studio-canvas .react-flow')).toBeVisible()
  await expect(page.getByRole('complementary', { name: 'Inspector' })).toContainText('Nothing selected')
  await expect(page.getByRole('region', { name: 'Problems' })).toBeVisible()

  // Boundaries contain their children: every node sits inside the region group.
  const region = await page.locator('.react-flow__node-boundary', { hasText: 'ap-southeast-2' }).boundingBox()
  for (const name of ['shop-api', 'receipts', 'payment-jobs']) {
    const box = await node(page, name).boundingBox()
    expect(box.x).toBeGreaterThanOrEqual(region.x)
    expect(box.y).toBeGreaterThanOrEqual(region.y)
    expect(box.x + box.width).toBeLessThanOrEqual(region.x + region.width + 1)
    expect(box.y + box.height).toBeLessThanOrEqual(region.y + region.height + 1)
  }

  // The library rail searches names, aliases and vendors.
  const rail = page.getByRole('complementary', { name: 'Node library' })
  await rail.getByLabel('Search the node library').fill('parameter store')
  await expect(rail.getByRole('button', { name: /AWS Systems Manager Parameter Store/ })).toBeVisible()
  await rail.getByRole('button', { name: /AWS Systems Manager Parameter Store/ }).click()
  await expect(page.getByRole('complementary', { name: 'Inspector' })).toContainText('identity.ssm-parameter-store@1')
  await rail.getByLabel('Search the node library').fill('zzz-nothing')
  await expect(rail.getByText('No type matches')).toBeVisible()
  await rail.getByLabel('Search the node library').fill('')

  // Selecting a node shows its properties by tier and its state in words.
  await node(page, 'shop-api').click()
  const inspector = page.getByRole('complementary', { name: 'Inspector' })
  await expect(inspector.getByRole('heading', { name: 'shop-api' })).toBeVisible()
  await expect(inspector).toContainText('Described')
  await expect(inspector).toContainText('Serverless function (service.function@2)')
  await expect(inspector.getByRole('heading', { name: 'Required' })).toBeVisible()
  await expect(inspector.locator('#prop-runtime')).toHaveValue('python 3.12')

  // A Draft node says what it still needs, on the canvas and in the problems list.
  const drawer = page.getByRole('region', { name: 'Problems' })
  await drawer.getByRole('button', { name: /Problems/ }).click()
  const images = drawer.getByRole('listitem').filter({ hasText: 'payments-worker images' })
  await expect(images).toContainText('Needs Repository name')
  await expect(drawer).toContainText('Needs API ID')
  await images.getByRole('button').click()
  await expect(inspector.getByRole('heading', { name: 'payments-worker images' })).toBeVisible()
  await expect(inspector).toContainText('Stays Draft until Repository name is filled.')
  await expect(node(page, 'payments-worker images').locator('.estate-node.draft')).toContainText('Draft')
  await expect(page.locator('.boundary-node.draft', { hasText: 'proving-ground account' })).toContainText('Draft')

  // Keyboard only: Tab reaches a node, Enter selects it.
  await page.keyboard.press('Escape')
  await expect(inspector).toContainText('Nothing selected')
  await page.locator('.react-flow__node').first().focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await expect(inspector).not.toContainText('Nothing selected')

  await page.keyboard.press('Escape')
  await drawer.getByRole('button', { name: /Problems/ }).click()
  // The overview hides text cleanly instead of shrinking it below 11 px.
  await page.getByRole('button', { name: 'Fit to screen' }).click()
  await expect(page.locator('.estate-node.full')).toHaveCount(0)
  await assertReadable(page)
  // Zoomed in, the full cards show every line at 11 px or more.
  for (let i = 0; i < 6; i++) await page.getByRole('button', { name: 'Zoom In' }).click()
  await expect(page.locator('.estate-node.full').first()).toBeVisible()
  await assertReadable(page)
  await assertAccessible(page)
  await quiet(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.reload()
  await expect(page.locator('.estate-node').first()).toBeVisible()
  await assertReadable(page)
  await shot(page, 'studio-1440.png')
  await drawer.getByRole('button', { name: /Problems/ }).click()
  await drawer.getByRole('listitem').filter({ hasText: 'receipts' }).getByRole('button').click()
  await expect(inspector.getByRole('heading', { name: 'receipts' })).toBeVisible()
  await drawer.getByRole('button', { name: /Problems/ }).click()
  await shot(page, 'studio-selected-1440.png')
  await page.reload()
  await expect(page.locator('.estate-node').first()).toBeVisible()
  await shoot(page, 'studio', { mask: [page.locator('.studio-version')] })
  gate.assertClean()

  // Checked after the clean run, because the refusal is an expected 422.
  // A clone that names another company's vault is refused, and the screen says why.
  await page.getByTestId('clone-file').setInputFiles(await exampleFile(info.outputDir, 'other.clone.json',
    (text) => text.replace('"clone": "proving-ground"', '"clone": "other"').replace('vault://tenant/acme/aws-readonly', 'vault://tenant/globex/aws-readonly')))
  await expect(page.locator('.toast.error', { hasText: 'vault reference belongs to another company' })).toBeVisible()
  await expect(page.locator('.estate-node')).toHaveCount(24)

})

test('below 1024 px the library slides over and the inspector is a bottom sheet', async ({ page, app }, info) => {
  await mkdir(SHOTS, { recursive: true })
  await withClone(page, app, info)
  await page.setViewportSize({ width: 768, height: 900 })
  await page.goto(`${app.baseURL}/studio`)
  await expect(page.locator('.estate-node').first()).toBeVisible()
  await expect(page.getByRole('complementary', { name: 'Node library' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Library' }).click()
  await expect(page.getByRole('complementary', { name: 'Node library' })).toBeVisible()
  await shot(page, 'library-768.png')
  await page.getByRole('button', { name: 'Close the library' }).click()
  await expect(page.getByRole('complementary', { name: 'Node library' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Library' }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('complementary', { name: 'Node library' })).toHaveCount(0)
  await node(page, 'receipts').click()
  const sheet = page.getByRole('complementary', { name: 'Inspector' })
  await expect(sheet.getByRole('heading', { name: 'receipts' })).toBeVisible()
  await shot(page, 'inspector-768.png')
  await sheet.getByRole('button', { name: 'Close the inspector' }).click()
  await expect(sheet).toHaveCount(0)

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByText('On a phone the Studio is view only. Editing needs a wider screen.')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  for (const name of ['Library', 'Fit to screen', 'Export']) {
    const box = await page.getByRole('button', { name }).boundingBox()
    expect(box.height).toBeGreaterThanOrEqual(44)
  }
  await shot(page, 'studio-390.png')
  await assertAccessible(page)
})

test('someone who cannot edit the canvas sees the clone but cannot import', async ({ page, app }, info) => {
  await withClone(page, app, info)
  await page.getByRole('button', { name: 'Sign out' }).click()
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/studio`)
  await expect(page.locator('.estate-node').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Import a clone' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'New clone' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Export' })).toBeEnabled()
  await page.getByRole('link', { name: 'How this works' }).click()
  await expect(page.locator('.doc').getByRole('heading', { level: 1, name: 'Clone Studio' })).toBeVisible()
  await expect(page.locator('.doc')).toContainText('Draft and Described')
})

const PLACE = path.resolve(HERE, '../screenshots/P2.2')

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

async function placeKind(page, query, typeName, itemName) {
  const rail = page.getByRole('complementary', { name: 'Node library' })
  await replaceField(page, '[aria-label="Search the node library"]', query)
  const button = rail.getByRole('button', { name: typeName })
  await expect(button).toBeVisible()
  await button.focus()
  // Enter on a library type places it, the keyboard way of dropping it (STUDIO.1).
  await page.keyboard.press('Enter')
  await expect(page.locator('#item-name')).toBeVisible()
  await replaceField(page, '#item-name', itemName)
  await expect(node(page, itemName)).toBeVisible()
}

async function nodeX(page, name) {
  return node(page, name).evaluate((el) => {
    const transform = el.style.transform
    const start = transform.indexOf('translate(')
    if (start < 0) return 0
    let number = ''
    for (const ch of transform.slice(start + 'translate('.length)) {
      if ((ch >= '0' && ch <= '9') || ch === '-' || ch === '.') number += ch
      else break
    }
    return Number(number)
  })
}

async function selectItem(page, name) {
  await node(page, name).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('complementary', { name: 'Inspector' }).getByRole('heading', { name })).toBeVisible()
}

async function draw(page, from, to) {
  await selectItem(page, from)
  await page.getByRole('button', { name: 'Draw a connection' }).focus()
  await page.keyboard.press('Enter')
  await pick(page, '#edge-target', to)
}

// Last in this file on purpose: it creates a second clone, and the empty-state check above must run first.
test('an engineer places three nodes, connects them and describes each one', async ({ page, app }, info) => {
  test.setTimeout(180_000)
  await mkdir(PLACE, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await withClone(page, app, info)
  let creates = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/clones') creates += 1
  })

  await page.getByRole('button', { name: 'New clone' }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Create clone' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('alert')).toHaveText('Enter a short name.')
  expect(creates).toBe(0)

  await replaceField(page, '#clone-name', 'Hand built')
  await page.getByRole('button', { name: 'Create clone' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('toolbar', { name: 'Clone' })).toContainText('hand-built')
  await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible()
  expect(creates).toBe(1)
  await quiet(page)
  await page.screenshot({ path: path.join(PLACE, 'empty-1440.png'), fullPage: true })

  await placeKind(page, 'backend service', 'Backend service or API', 'payments-api')
  await placeKind(page, 'aurora', 'Amazon Aurora PostgreSQL', 'orders-db')
  await placeKind(page, 'GitHub repository', 'GitHub repository', 'shop-repo')
  await expect(page.locator('.estate-node.draft')).toHaveCount(3)

  await selectItem(page, 'payments-api')
  const before = await nodeX(page, 'payments-api')
  await node(page, 'payments-api').focus()
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => nodeX(page, 'payments-api')).toBe(before + 16)

  await draw(page, 'payments-api', 'orders-db')
  await expect(page.locator('#edge-type')).toContainText('Reads')
  await page.getByRole('button', { name: 'Add connection' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Reads' })).toBeVisible()

  await draw(page, 'payments-api', 'shop-repo')
  await expect(page.locator('#edge-type')).toContainText('Deploys from')
  await page.getByRole('button', { name: 'Add connection' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Deploys from' })).toBeVisible()

  await draw(page, 'orders-db', 'shop-repo')
  await expect(page.locator('#edge-type')).toContainText('Depends on')
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await pick(page, '#edge-type', 'Publishes to')
  const refusal = page.getByRole('dialog').getByRole('alert')
  await expect(refusal).toContainText('cannot start a Publishes to connection')
  await expect(refusal).toContainText('cannot receive a Publishes to connection')
  await expect(refusal).toContainText('A Generic relation is allowed instead.')
  await expect(page.getByRole('button', { name: 'Add connection' })).toBeDisabled()
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await page.screenshot({ path: path.join(PLACE, 'refusal-1440.png'), fullPage: true })
  await pick(page, '#edge-type', 'Generic relation')
  await expect(page.getByRole('dialog').getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: 'Add connection' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Generic relation' })).toBeVisible()
  await expect(page.locator('.react-flow__edge')).toHaveCount(3)

  await selectItem(page, 'payments-api')
  await pick(page, '#prop-kind', 'api')
  await replaceField(page, '#prop-runtime', 'python 3.12')
  await pick(page, '#prop-exposure', 'internal')
  await selectItem(page, 'orders-db')
  await replaceField(page, '#prop-cluster_identifier', 'orders')
  await replaceField(page, '#prop-region', 'ap-southeast-2')
  await pick(page, '#prop-engine', 'aurora-postgresql')
  await selectItem(page, 'shop-repo')
  await replaceField(page, '#prop-url', 'https://github.com/acme/shop')
  await pick(page, '#prop-auth_method', 'github_app')

  await expect(page.locator('.estate-node.described')).toHaveCount(3)
  await expect(page.locator('.estate-node.draft')).toHaveCount(0)
  await expect(page.getByRole('complementary', { name: 'Inspector' })).toContainText('Described')
  await expect(page.getByRole('complementary', { name: 'Inspector' })).not.toContainText('Stays Draft')
  await expect(page.getByRole('region', { name: 'Problems' })).toContainText('None')
  await quiet(page)
  await page.screenshot({ path: path.join(PLACE, 'described-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByText('On a phone the Studio is view only.')).toBeVisible()
  await page.screenshot({ path: path.join(PLACE, 'described-390.png'), fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.getByRole('button', { name: 'New clone' })).toBeVisible()

  const rail = page.getByRole('complementary', { name: 'Node library' })
  await replaceField(page, '[aria-label="Search the node library"]', 'sqs')
  await rail.locator('[data-type="messaging.sqs"]').dragTo(page.locator('.react-flow__pane'))
  await expect(node(page, 'Amazon SQS queue')).toBeVisible()
  await selectItem(page, 'Amazon SQS queue')
  await page.keyboard.press('Delete')
  await expect(node(page, 'Amazon SQS queue')).toHaveCount(0)
  await page.locator('.toast').getByRole('button', { name: 'Undo' }).click()
  await expect(node(page, 'Amazon SQS queue')).toBeVisible()

  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  await page.reload()
  await expect(node(page, 'payments-api')).toBeVisible()
  await expect(node(page, 'orders-db')).toBeVisible()
  await expect(node(page, 'shop-repo')).toBeVisible()
  await expect(node(page, 'Amazon SQS queue')).toBeVisible()
  await expect(page.locator('.estate-node.described')).toHaveCount(3)
  await expect(page.locator('.react-flow__edge')).toHaveCount(3)
  await page.keyboard.press('Escape')
  await quiet(page)
  await assertAccessible(page)
  await shoot(page, 'place', { mask: [page.locator('.studio-version')] })
  gate.assertClean()
})
