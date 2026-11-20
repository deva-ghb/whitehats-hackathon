/** Clickable rows are real links: keyboard, a new tab, and the other three tables. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, tabTo } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-011')

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

test('table rows are real links', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/incidents`)
  const first = page.locator('table').getByRole('link').first()
  await expect(first).toHaveAttribute('href', /\/incidents\/\d+/)
  await assertAccessible(page)
  await shoot(page, 'incidents')

  await tabTo(page, first)
  const outline = await first.evaluate((el) => getComputedStyle(el.closest('tr')).outlineStyle)
  expect(outline).not.toBe('none')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/incidents\/\d+$/)

  await page.goto(`${app.baseURL}/incidents`)
  const href = await first.getAttribute('href')
  const cancelled = await first.evaluate((el) => {
    const modified = new MouseEvent('click', { bubbles: true, cancelable: true, metaKey: true, button: 0 })
    el.dispatchEvent(modified)
    const middle = new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 })
    el.dispatchEvent(middle)
    return modified.defaultPrevented || middle.defaultPrevented
  })
  expect(cancelled).toBe(false)
  await expect(page).toHaveURL(/\/incidents$/)
  const opened = await page.context().newPage()
  await opened.goto(`${app.baseURL}${href}`)
  await expect(opened).toHaveURL(/\/incidents\/\d+/)
  await expect(opened.locator('h2').first()).toBeVisible()
  await opened.close()

  const cell = page.locator('table tbody tr').first().getByRole('cell').nth(1)
  await cell.click({ button: 'right' })
  await expect(page).toHaveURL(/\/incidents$/)
  await cell.click()
  await expect(page).toHaveURL(/\/incidents\/\d+$/)

  const created = await page.evaluate(async () => {
    const res = await fetch('/api/collectors', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${localStorage.getItem('wh.token')}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ name: 'Edge logs', kind: 'log', mode: 'docker' }),
    })
    const data = await res.json()
    return { status: res.status, token: data.token || '', id: data.collector?.id }
  })
  expect(created.status).toBe(200)
  try {
    await page.goto(`${app.baseURL}/collectors`)
    await expect(page.getByRole('link', { name: 'Edge logs' })).toHaveAttribute('href', /\/collectors\/\d+/)
    await shoot(page, 'collectors')

    const ingested = await page.evaluate(async (token) => {
      const res = await fetch('/api/ingest/logs', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          host: 'edge',
          lines: [{ source: 'auth', message: 'authentication failure for user root' }],
        }),
      })
      return res.status
    }, created.token)
    expect(ingested).toBe(200)

    await page.goto(`${app.baseURL}/collectors/logs`)
    const shape = page.getByRole('link', { name: /authentication failure/ })
    await expect(shape).toHaveAttribute('href', /\/logs\?shape=\d+/)
    await shape.click()
    await expect(page.getByRole('dialog', { name: 'Lines with this shape' })).toBeVisible()
    await expect(page.getByRole('dialog')).toContainText('authentication failure for user root')
    await shoot(page, 'log-shape')
  } finally {
    // A collector that reported makes WhiteHats count as watching for every later test on this database.
    await page.evaluate(async (id) => {
      await fetch(`/api/collectors/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}` },
      })
    }, created.id)
  }

  gate.assertClean()
})
