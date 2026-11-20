/** Connectors on screen and on the canvas (CN.5): connect AWS, see what it found, take one permission away. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { startFakeAws } from '../fake-aws.js'
import { ensureConnections, grantEditing, openConnection } from '../connections.js'
import { WIDTHS, assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/CN.5')
const REVOKED = 'ec2:DescribeInstances'

// The app runs against a fake AWS account (moto with IAM enforced). Nothing here reaches real AWS.
const test = base.extend({
  aws: [async ({}, use) => {
    const aws = await startFakeAws()
    await use(aws)
    await aws.stop()
  }, { scope: 'worker' }],
  app: [async ({ aws }, use) => {
    const started = await startApp(aws.env)
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.describe.configure({ mode: 'serial' })

const estate = (page, name) => page.locator('.react-flow__node-estate').filter({ has: page.getByText(name, { exact: true }) })

async function openFound(page, baseURL) {
  await page.goto(`${baseURL}/studio`)
  // The clone with the connection node is the first; what the connections found (read only, with its marks) is the other.
  const picker = page.getByRole('combobox', { name: 'Clone' })
  await picker.click()
  await page.getByRole('option', { name: 'Found by connectors' }).click()
  await expect(page.getByText('Read only: what your connections found.')).toBeVisible()
  await expect(estate(page, 'admin-box')).toBeVisible()
}

async function shots(page, name) {
  await mkdir(SHOTS, { recursive: true })
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`), fullPage: true })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow, `${name} at ${width} px`).toBeLessThanOrEqual(1)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('AWS connects, its finds land on the canvas, and a revoked permission is named and marked', async ({ page, app, aws }) => {
  test.setTimeout(180_000)
  const gate = attachGate(page)
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/studio`)
  await expect(page.getByRole('heading', { name: 'No clone yet' })).toBeVisible()  // empty: nothing found yet

  // The connection is a node on the canvas: its setup is in the inspector, and connecting also finds what it can see.
  const card = await openConnection(page, app.baseURL, await ensureConnections(page, ['aws']), 'aws')
  await card.locator('#connection-role_arn').fill(aws.role_arn)
  await card.locator('#connection-external_id').fill('e2e-external-id')
  await card.locator('#connection-region').fill(aws.region)
  await card.getByRole('button', { name: 'Connect' }).click()
  await expect(card.getByTestId('permission-summary')).toContainText('All 19 permissions ok', { timeout: 30_000 })
  await expect(card.getByText('Credentials checked, not reading yet', { exact: true })).toBeVisible()
  await expect(card.getByText('Found 8 resources')).toBeVisible({ timeout: 30_000 })
  // Keyboard only: from the first control of the panel, a few Tabs reach Find resources.
  await card.locator('summary').first().focus()
  let reached = false
  for (let step = 0; step < 14 && !reached; step += 1) {
    await page.keyboard.press('Tab')
    reached = (await page.evaluate(() => document.activeElement?.textContent || '')).includes('Find resources')
  }
  expect(reached, 'Tab reaches Find resources').toBe(true)
  await assertAccessible(page)

  await openFound(page, app.baseURL)
  for (const name of ['fintech-receipts', 'orders', 'payment-jobs']) await expect(estate(page, name)).toBeVisible()
  await expect(page.locator('.estate-state.generic')).toHaveText(['Needs a type'])
  await expect(page.locator('.estate-state.problem')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Export' })).toHaveCount(0)
  await assertAccessible(page)
  await shots(page, 'found-before')

  await aws.revoke(REVOKED)
  const again = await openConnection(page, app.baseURL, await ensureConnections(page, ['aws']), 'aws')
  await again.getByRole('button', { name: 'Check again' }).click()
  await expect(again.getByTestId('permission-summary')).toContainText('1 of 19 permissions missing', { timeout: 20_000 })
  const missing = again.getByRole('list', { name: 'Missing permissions' })
  await expect(missing.getByRole('listitem')).toHaveText([`${REVOKED} is missing. WhiteHats needs it to read EC2 instance settings: size, address, instance profile.`])
  await expect(again.getByText('Check failed', { exact: true })).toBeVisible()
  await assertAccessible(page)
  await shots(page, 'connection-after')

  await openFound(page, app.baseURL)
  await expect(page.getByText(`1 node may be out of date: AWS is missing ${REVOKED}.`)).toBeVisible()
  // At the opening zoom the card shows its name and the "!" disc; zoomed in, the badge says it in words.
  await expect(estate(page, 'admin-box').locator('.estate-alert')).toBeVisible()
  await expect(page.locator('.estate-alert')).toHaveCount(1)
  expect(await estate(page, 'admin-box').getAttribute('aria-label')).toContain('connector problem: AWS cannot read this any more')
  for (let step = 0; step < 3; step += 1) await page.getByRole('button', { name: 'Zoom In' }).click()
  await expect(estate(page, 'admin-box').getByText('Out of date', { exact: true })).toBeVisible()
  await estate(page, 'admin-box').click()
  const inspector = page.getByRole('complementary', { name: 'Inspector' })
  await expect(inspector.getByText(`AWS cannot read this any more: ${REVOKED} is missing.`)).toBeVisible()
  await expect(inspector.getByText('discovered, from aws')).toBeVisible()
  await expect(inspector.getByText('Last confirmed')).toBeVisible()
  await assertAccessible(page)
  await shots(page, 'found-after')
  gate.assertClean()
})

test('installing GitHub says plainly when the App is not set up on this server', async ({ page, app }) => {
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL)
  const card = await openConnection(page, app.baseURL, await ensureConnections(page, ['github']), 'github')
  await expect(card.getByRole('button', { name: 'Connect' })).toHaveCount(0)
  await card.getByRole('button', { name: 'Install the GitHub App' }).click()
  await expect(page.getByText('The WhiteHats GitHub App is not set up on this server yet.')).toBeVisible()
  await mkdir(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, 'github-not-set-up-1440.png'), fullPage: true })
})
