/** Coverage is unmeasured, and planned WhiteHats sit in their own section. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-019')
const WATCH = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-032')

test('whitehats say coverage is not measured and planned ones are separate', async ({ page, app }) => {
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/agents`)
  await expect(page.getByRole('heading', { level: 1, name: 'WhiteHats' })).toBeVisible()
  await expect(page.getByText('Not measured yet').first()).toBeVisible()
  await expect(page.locator('.pill', { hasText: 'Waiting for data' })).toHaveCount(5)
  await expect(page.locator('.card.stat', { hasText: 'Watching' })).toContainText('0/5')
  await expect(page.locator('.card.stat', { hasText: 'Blind spots' })).toContainText('5')
  await expect(page.getByRole('heading', { name: 'Planned' })).toBeVisible()
  await expect(page.getByText('Application Security')).toBeVisible()
  await expect(page.getByText('Flags a burst of failed sign-ins in collected log lines.')).toBeVisible()
  await expect(page.getByText(/lateral movement/i)).toHaveCount(0)
  await expect(page.getByText(/tampering/i)).toHaveCount(0)
  await expect(page.getByText(/privilege changes/i)).toHaveCount(0)
  await expect(page.getByText('90%')).toHaveCount(0)
  await expect(page.getByText('Azure')).toHaveCount(0)
  const planned = page.locator('section.card.wh-planned, .card.wh-planned')
  await expect(planned).toHaveCount(8)
  for (let i = 0; i < 8; i++) {
    const card = planned.nth(i)
    await expect(card.getByText('Coverage')).toHaveCount(0)
    await expect(card.getByRole('button')).toHaveCount(0)
  }
  await assertAccessible(page)

  await mkdir(SHOTS, { recursive: true })
  await mkdir(WATCH, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'whitehats-1440.png'), fullPage: true })
  await page.screenshot({ path: path.join(WATCH, 'whitehats-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, 'whitehats-390.png'), fullPage: true })
  await page.screenshot({ path: path.join(WATCH, 'whitehats-390.png'), fullPage: true })
})

test('working hours save and survive a reload', async ({ page, app }) => {
  // Working hours are the company's, used by every project, so an Account owner sets them (R-041).
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/setup`)
  await expect(page.getByRole('heading', { name: 'Working hours' })).toBeVisible()
  await expect(page.getByText('Until you save a change, Insider uses 09:00 to 17:00 UTC.')).toBeVisible()
  await page.locator('#work-zone').click()
  await page.getByLabel('Search options').fill('Kolkata')
  await page.getByRole('option', { name: 'Kolkata' }).click()
  await page.locator('#work-start').click()
  await page.getByRole('option', { name: '10:00' }).click()
  await page.locator('#work-end').click()
  await page.getByRole('option', { name: '18:00' }).click()
  await page.getByRole('button', { name: 'Save working hours' }).click()
  await expect(page.getByText('Working hours saved')).toBeVisible()
  await page.reload()
  await expect(page.locator('#work-zone')).toContainText('Kolkata')
  await expect(page.locator('#work-start')).toContainText('10:00')
  await expect(page.locator('#work-end')).toContainText('18:00')
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.locator('#work-zone').click()
  await page.getByRole('option', { name: 'UTC' }).click()
  await page.locator('#work-start').click()
  await page.getByRole('option', { name: '09:00' }).click()
  await page.locator('#work-end').click()
  await page.getByRole('option', { name: '17:00' }).click()
  await page.getByRole('button', { name: 'Save working hours' }).click()
  await expect(page.getByText('Working hours saved')).toBeVisible()
})
