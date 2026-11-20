/** Status and role changes ask before they save, and refuse the dangerous ones. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-010')

async function shoot(page, name) {
  await mkdir(SHOTS, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, `${name}-1440.png`) })
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, `${name}-390.png`) })
  await page.setViewportSize({ width: 1440, height: 900 })
}

async function callApi(page, path, body) {
  return page.evaluate(async ({ path, body }) => {
    const res = await fetch(path, {
      method: 'PATCH',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${localStorage.getItem('wh.token')}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    return { status: res.status, detail: typeof data.detail === 'string' ? data.detail : '' }
  }, { path, body })
}

test('status and role changes are deliberate', async ({ page, app }) => {
  await signIn(page, app.baseURL)

  await page.goto(`${app.baseURL}/incidents`)
  await page.getByRole('row', { name: /INC-2208/ }).click()
  await expect(page.locator('.page-head')).toContainText('investigating')
  // FD.2: a person can only say it is fixed (WhiteHats then checks and resolves it); never resolve it by hand, and not
  // while a proposed fix still waits for a decision.
  await page.locator('#change-status').click()
  await page.locator('#incident-status').click()
  await expect(page.getByRole('option', { name: /Resolved/ })).toHaveCount(0)
  await expect(page.getByRole('option', { name: /Fixed, please verify/ })).toBeVisible()
  await expect(page.getByText(/Still waiting on a decision/)).toBeVisible()
  await page.keyboard.press('Escape')
  await page.locator('#status-note').fill('The probing host is isolated for now')
  await shoot(page, 'change-status')
  await expect(page.getByRole('button', { name: 'Update status' })).toBeDisabled()
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.locator('.page-head')).toContainText('investigating')

  await page.goto(`${app.baseURL}/settings`)
  await page.getByRole('tab', { name: 'People' }).click()
  const anita = page.getByRole('row', { name: /Anita Rao/ })
  await expect(anita.getByRole('button', { name: 'Change role' })).toHaveCount(0)
  const users = await page.evaluate(async () => {
    const res = await fetch('/api/admin/users', {
      headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}` },
    })
    return res.json()
  })
  const me = users.find((u) => u.email === 'ceo@acme.example')
  const own = await callApi(page, `/api/admin/users/${me.id}`, { role: 'analyst' })
  expect(own.status).toBe(409)
  expect(own.detail).toContain('own role')

  const priyanka = page.getByRole('row', { name: /Priyanka Das/ })
  await priyanka.getByRole('button', { name: 'Change role' }).click()
  const dialog = page.getByRole('dialog', { name: "Change Priyanka Das's role" })
  await dialog.locator('button').first().waitFor()
  await dialog.locator('[id^="role-next-"]').click()
  await page.getByRole('option', { name: 'Engineer' }).click()
  await expect(dialog).toContainText('Change Priyanka Das from Analyst to Engineer?')
  await expect(dialog).toContainText('Engineer in the projects they are added to. Cannot approve fixes.')
  await shoot(page, 'role-confirm')
  await dialog.getByRole('button', { name: 'Change role' }).click()
  await expect(page.getByText('Role updated')).toBeVisible()
  await expect(priyanka).toContainText('Engineer')

  await page.getByRole('tab', { name: 'Audit trail' }).click()
  const audit = page.getByRole('row', { name: /changed role/ })
  await expect(audit).toContainText('Anita Rao')
  await expect(audit).toContainText('Priyanka Das')
  await expect(audit).toContainText('analyst to engineer')

  await page.getByRole('tab', { name: 'People' }).click()
  const meera = page.getByRole('row', { name: /Meera Pillai/ })
  await meera.getByRole('button', { name: 'Change role' }).click()
  const meeraDialog = page.getByRole('dialog', { name: "Change Meera Pillai's role" })
  await meeraDialog.locator('[id^="role-next-"]').click()
  await page.getByRole('option', { name: 'Analyst' }).click()
  await meeraDialog.getByRole('button', { name: 'Change role' }).click()
  await expect(meera).toContainText('Engineer in every project')

  // Put both people back, so later tests and their screenshots see the demo as seeded.
  const byEmail = Object.fromEntries(users.map((u) => [u.email, u.id]))
  expect((await callApi(page, `/api/admin/users/${byEmail['analyst@acme.example']}`, { role: 'analyst' })).status).toBe(200)
  expect((await callApi(page, `/api/admin/users/${byEmail['ciso@acme.example']}`, { role: 'ciso' })).status).toBe(200)
})
