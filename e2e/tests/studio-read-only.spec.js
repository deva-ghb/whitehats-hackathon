/** The connections draw the map (STUDIO.7): the Studio is the scene alone, read only, and Connect a source starts the map. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { startFakeAws } from '../fake-aws.js'
import { WIDTHS, assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/STUDIO.7')

// The app runs against a fake AWS account (moto with IAM enforced). Nothing here reaches real AWS.
const test = base.extend({
  aws: [async ({}, use) => {
    const aws = await startFakeAws({ withFunction: true })
    await use(aws)
    await aws.stop()
  }, { scope: 'worker' }],
  app: [async ({ aws }, use) => {
    const started = await startApp({ ...aws.env, WH_STUDIO_HAND_EDITING: '0' })
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

const inspector = (page) => page.getByRole('complementary', { name: 'Inspector' })
const panel = (page) => inspector(page).getByRole('region', { name: 'AWS connection' })
const solids = (page) => page.locator('[data-scene-item]')

async function shots(page, name) {
  await mkdir(SHOTS, { recursive: true })
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`) })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('the account owner connects AWS from the Studio and the map draws itself; nothing can be drawn by hand', async ({ page, app, aws }) => {
  test.setTimeout(180_000)
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  // The owner sets connections up but holds no right to edit the model: before STUDIO.7 they could find but not bring in.
  await signIn(page, app.baseURL, 'ceo@acme.example', 'plan')
  await page.goto(`${page.url().split('/p/')[0]}/p/main/studio`)

  // No map yet: the only way in is to connect a source. Nothing offers to draw or import by hand.
  await expect(page.getByRole('heading', { name: 'No map yet' })).toBeVisible()
  for (const name of ['Start a blank clone', 'Import a clone', 'Import an estate']) await expect(page.getByRole('button', { name })).toHaveCount(0)
  await shots(page, 'no-map')

  await page.getByRole('button', { name: 'Connect a source' }).click()
  const dialog = page.getByRole('dialog', { name: 'Connect a source' })
  await expect(dialog).toBeVisible()
  await assertAccessible(page)
  // The three sources read as one list: every name and description starts at the same left edge.
  const lefts = await dialog.locator('.connect-source').evaluateAll((options) => options.flatMap((option) =>
    [...option.children].map((line) => Math.round(line.getBoundingClientRect().left - option.getBoundingClientRect().left))))
  expect(new Set(lefts).size).toBe(1)
  await shots(page, 'connect-a-source')
  await dialog.getByRole('button', { name: /^AWS Your services/ }).click()

  // The map starts with the AWS connection on it, selected, and its setup open beside it. The remembered plan view is not used.
  await expect(page.getByRole('application', { name: /Isometric scene/ })).toBeVisible()
  await expect(page.locator('[data-scene-item][data-id="g_connection_aws"]')).toHaveCount(1)
  await expect(panel(page)).toContainText('Not connected')
  await expect(page.getByRole('complementary', { name: 'Node library' })).toHaveCount(0)
  for (const name of ['Plan view', 'Scene view', 'Undo last change', 'New clone']) await expect(page.getByRole('button', { name })).toHaveCount(0)
  const search = page.getByRole('textbox', { name: 'Search the map (coming soon)' })
  await expect(search).toBeDisabled()
  await expect(page.locator('.studio-search-soon')).toContainText('Coming soon')
  await expect(page.locator('.studio-readonly')).toContainText('WhiteHats draws this map from your connections')
  await shots(page, 'connection-placed')

  // Set it up. Connecting finds what the role can see and brings it onto the map, with nobody drawing anything.
  const setup = panel(page)
  await setup.locator('#connection-role_arn').fill(aws.role_arn)
  await setup.locator('#connection-external_id').fill('e2e-external-id')
  await setup.locator('#connection-region').fill(aws.region)
  await setup.getByRole('button', { name: 'Connect' }).click()
  await expect(setup.getByTestId('connection-found')).toContainText('inside this node', { timeout: 30_000 })
  await expect.poll(() => solids(page).count(), { timeout: 30_000 }).toBeGreaterThan(5)
  await page.waitForTimeout(800)
  await assertAccessible(page)
  await shots(page, 'drawn-from-aws')

  // The saved map holds the connector's name only, never the role or the external id, and every version is the connection's.
  const saved = await page.evaluate(async () => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('wh.token')}` }
    const list = await (await fetch('/api/clones?project=main', { headers })).json()
    const versions = await (await fetch(`/api/clones/${list.clones[0].id}/versions?project=main`, { headers })).json()
    const document = (await (await fetch(`/api/clones/${list.clones[0].id}?project=main`, { headers })).json()).document
    return { slug: list.clones[0].clone, text: JSON.stringify(document), reasons: versions.versions.map((version) => version.reason) }
  })
  expect(saved.slug).toBe('main')
  expect(saved.text).toContain('"connector":"aws"')
  expect(saved.text).not.toContain('e2e-external-id')
  expect(saved.text).not.toContain(aws.role_arn)
  expect(saved.reasons.slice(0, 2)).toEqual(['Started the Fintech shop map from its connections', 'Placed the AWS connection'])
  gate.assertClean()
})
