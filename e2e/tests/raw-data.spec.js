/** Raw logs, flows and collector feeds belong to no project yet, so a project member is told why they are not shown (AP.2). */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/AP.2')
const WHO = 'Account owners, Account admins, Security leads and Auditors'

async function evidence(page, name) {
  await mkdir(SHOTS, { recursive: true })
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`), fullPage: true })
    if (width === 390) {
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(1)
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('a project engineer is told why raw logs, flows and collector feeds are not shown', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example')

  await page.goto(`${app.baseURL}/p/main/collectors/logs`)
  await expect(page.getByText('Raw logs are kept at account level')).toBeVisible()
  await expect(page.getByText(WHO)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Build digest' })).toHaveCount(0)
  await assertAccessible(page)
  await evidence(page, 'logs-engineer')

  await page.goto(`${app.baseURL}/p/main/collectors/network`)
  await expect(page.locator('.banner', { hasText: 'not tied to one project yet' })).toBeVisible()
  await expect(page.getByText('Open findings')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Connections' })).toHaveCount(0)
  await assertAccessible(page)
  await evidence(page, 'network-engineer')

  await page.goto(`${app.baseURL}/p/main/collectors`)
  await expect(page.getByRole('heading', { level: 2, name: 'Collectors and pods' })).toBeVisible()
  // The demo has no collectors, so the list is the empty state; opening one is covered by the backend tests.
  await expect(page.getByText('No collectors yet')).toBeVisible()
  await assertAccessible(page)
  await evidence(page, 'collectors-engineer')
  gate.assertClean()
})

test('an account owner still reads raw logs and network connections', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/p/main/collectors/logs`)
  await expect(page.getByRole('button', { name: 'Build digest' })).toBeVisible()
  await expect(page.getByText('Raw logs are kept at account level')).toHaveCount(0)
  await page.goto(`${app.baseURL}/p/main/collectors/network`)
  await expect(page.locator('.banner', { hasText: 'not tied to one project yet' })).toHaveCount(0)
  await expect(page.getByText('Listening ports')).toBeVisible()
  gate.assertClean()
})
