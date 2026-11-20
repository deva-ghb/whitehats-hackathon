/** A replayed Fix Factory run says it is a recording, on the run, the timeline and the result. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-022')
const LABEL = 'Recorded run, replayed'
const row = {
  id: 'replay-1',
  item_id: 'WH-INT-2208-1',
  title: 'SQL injection in user lookup',
  mode: 'replay',
  status: 'verified',
  approval: 'pending',
  score: 85,
  cost_usd: 0,
  seconds: 4,
  started_at: '2026-10-23T03:00:00Z',
  finished_at: '2026-10-23T03:00:04Z',
}

test('a replayed run is labelled as a recording', async ({ page, app }) => {
  await page.route('**/api/factory/**', (route) => {
    const url = route.request().url()
    if (url.includes('/status')) {
      return route.fulfill({ json: { connected: true, summary: { verified: 1, runs: 1, approved: 0 } } })
    }
    if (url.includes('/runs/replay-1')) {
      return route.fulfill({ json: {
        row,
        events: [
          { stage: 'intake', level: 'ok', message: `${LABEL}. Replay of live-1: SQL injection in user lookup` },
          { stage: 'done', level: 'ok', message: `${LABEL}. verified, score 85/100` },
        ],
        verification: [{ check: 'Proof of weakness', before: 'failed', after: 'passed' }],
        review: null,
        diff: '',
      } })
    }
    if (url.includes('/inbox')) {
      return route.fulfill({ json: [{
        id: 'WH-INT-2208-1',
        title: row.title,
        severity: 'high',
        summary: 'find_user() builds its SQL by pasting the caller input into the query text.',
        latest_run: row,
        can_replay: true,
      }] })
    }
    if (url.includes('/runs')) {
      return route.fulfill({ json: { summary: { verified: 1, runs: 1, approved: 0 }, runs: [row] } })
    }
    return route.fulfill({ json: {} })
  })

  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/fix-factory`)
  await page.getByText(row.title, { exact: true }).click()
  const panel = page.locator('section.card', { has: page.getByRole('heading', { name: row.title }) })
  await expect(panel.getByText(`${LABEL} · replay-1`)).toBeVisible()
  await expect(panel.locator('.mono', { hasText: 'replay' })).toBeVisible()
  await expect(panel.getByText(`${LABEL}. Score`)).toBeVisible()
  await expect(panel.getByText(`${LABEL}. Replay of live-1`)).toBeVisible()
  await expect(panel.getByText(`${LABEL}. verified, score 85/100`)).toBeVisible()
  await assertAccessible(page)

  await mkdir(SHOTS, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'replay-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, 'replay-390.png'), fullPage: true })
})
