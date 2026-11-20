/** Only AWS and GitHub can be connected; every other connector, and nothing else in the menu, looks available (PT.1). On the canvas, since STUDIO.4. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { CONNECTORS, ensureConnections, grantEditing, openConnection } from '../connections.js'
import { assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

// Its own app: the owner is made a project admin and a clone is made, neither of which the other specs on the shared app may see.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/PT.1')

const PROTOTYPE = ['aws', 'cloudwatch', 'github']
const COMING_SOON = ['okta', 'entra', 'google', 'azure_monitor', 'gcp_logging', 'crowdstrike', 'jira']
// Every entry of the main menu opens a working screen. A screen that is not built has no entry.
const MENU = [
  'Overview', 'Audits', 'Ask Big Hat', 'Approvals', 'Incidents', 'Fix Factory', 'Registry', 'Clone Studio',
  'Knowledge sphere', 'Collectors', 'WhiteHats', 'Setup', 'AI models', 'Board report', 'Settings', 'Help',
  'Projects', 'Project settings', 'Account settings',
]

test('only AWS and GitHub can be connected, and the rest say coming soon', async ({ page, app }) => {
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL)
  const clone = await ensureConnections(page, [...PROTOTYPE, ...COMING_SOON])
  for (const key of PROTOTYPE) {
    const card = await openConnection(page, app.baseURL, clone, key)
    await expect(card.getByText('Not connected', { exact: true })).toBeVisible()
    // GitHub is connected by installing the WhiteHats GitHub App, never with a token form (CN.5).
    await expect(card.getByRole('button', { name: key === 'github' ? 'Install the GitHub App' : 'Connect' })).toBeVisible()
  }
  for (const key of COMING_SOON) {
    const card = await openConnection(page, app.baseURL, clone, key)
    await expect(card.getByText('Coming soon', { exact: true })).toBeVisible()
    await expect(card.getByText(`${CONNECTORS[key][0]} can be placed and named on the canvas, but it cannot be connected in this version.`)).toBeVisible()
    await expect(card.getByRole('button', { name: 'Connect' })).toHaveCount(0)
    await expect(card.getByRole('button', { name: 'Read now' })).toHaveCount(0)
    await expect(card.locator('input')).toHaveCount(0)  // no setup to fill in
  }
  await expect(page.getByText('Reading data')).toHaveCount(0)
  await assertAccessible(page)

  await mkdir(SHOTS, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'coming-soon-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  // The phone layout follows a screen-size hook, which redraws a moment after the resize: wait for it, then measure.
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, 'coming-soon-390.png'), fullPage: true })
})

test('every main menu entry opens a working screen', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  const home = page.url()
  const menu = page.getByRole('navigation', { name: 'Main' })
  // A badge (the Approvals count) follows some labels, so each label must start with a known entry.
  const labels = (await menu.getByRole('link').allInnerTexts()).map((text) => text.trim())
  expect(labels.filter((text) => !MENU.some((name) => text.startsWith(name)))).toEqual([])
  for (const name of MENU) {
    await page.goto(home)  // the account screens show a shorter menu, so start each entry from the project Overview
    await menu.getByRole('link', { name }).first().click()
    await expect(page.locator('h1').first()).toBeVisible()
    await expect(page.getByText('Something went wrong')).toHaveCount(0)
  }
  gate.assertClean()
})
