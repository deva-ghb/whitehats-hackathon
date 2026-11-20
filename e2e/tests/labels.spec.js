/** The log analyzer names its budget, and the empty state offers the next step. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import AxeBuilder from '@axe-core/playwright'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible } from '../gate.js'
import { signIn } from '../sign-in.js'

const SCREENS = ['/', '/incidents', '/approvals', '/fix-factory', '/sphere', '/collectors', '/collectors/logs', '/collectors/network', '/agents', '/setup', '/models', '/reports', '/settings', '/assistant']

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-014')

test('the log analyzer explains its controls', async ({ page, app }) => {
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/collectors/logs`)
  await expect(page.getByLabel('Token budget')).toBeVisible()
  await expect(page.getByText('Token budget for the digest')).toBeVisible()
  await expect(page.getByLabel('Search raw lines')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Deploy a collector' })).toBeVisible()
  await assertAccessible(page)

  await mkdir(SHOTS, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'logs-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, 'logs-390.png'), fullPage: true })

  await page.getByRole('link', { name: 'Deploy a collector' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Collectors' })).toBeVisible()

  for (const screen of SCREENS) {
    await page.goto(`${app.baseURL}${screen}`)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    const results = await new AxeBuilder({ page }).withRules(['label']).analyze()
    const summary = results.violations.flatMap((item) => item.nodes.map((node) => `${screen} ${node.target.join(' ')} ${node.html}`))
    expect(summary, summary.join('\n')).toEqual([])
  }
  await page.goto(`${app.baseURL}/incidents`)
  await page.locator('table').getByRole('link').first().click()
  await expect(page.locator('#note')).toBeVisible()
  const detail = await new AxeBuilder({ page }).withRules(['label']).analyze()
  const detailSummary = detail.violations.flatMap((item) => item.nodes.map((node) => node.html))
  expect(detailSummary, detailSummary.join('\n')).toEqual([])
})
