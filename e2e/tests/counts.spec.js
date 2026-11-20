/** The briefing, the decision list, the badge and Approvals name one count. */

import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { signIn } from '../sign-in.js'

test('overview counts agree', async ({ page, app }) => {
  await signIn(page, app.baseURL)
  await expect(page.locator('.brief')).toBeVisible()
  const count = await page.evaluate(async () => {
    const res = await fetch('/api/overview', {
      headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}` },
    })
    return (await res.json()).decisions.length
  })
  expect(count).toBeGreaterThan(0)
  const shown = Math.min(count, 8)
  expect(await page.locator('.decision').count()).toBe(shown)
  await expect(page.locator('.brief')).toContainText(`${count} decisions wait for you`)
  await expect(page.getByRole('link', { name: /Approvals/ }).locator('.count')).toHaveText(String(count))
  if (count > shown) await expect(page.getByText(`and ${count - shown} more`, { exact: true })).toBeVisible()
  await expect(page.getByText(/points: Open incidents/)).toBeVisible()
  await expect(page.getByText('Last 7 days', { exact: true })).toBeVisible()

  await page.getByRole('link', { name: /Approvals/ }).click()
  await expect(page.getByRole('heading', { level: 2, name: `${count} decisions wait for you` })).toBeVisible()
})
