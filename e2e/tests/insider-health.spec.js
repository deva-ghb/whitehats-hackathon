/** Sign-in lines with no readable time are counted on the Insider Threat card. */

import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-038')
const NOTE = '2 sign-in lines had no readable time, so they were not judged.'

function resetHealth(dataDir) {
  execFileSync('python3', ['-c', `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("update agents set health = '{}' where key = 'insider'")
db.commit()
`, path.join(dataDir, 'whitehats.db')], { encoding: 'utf8' })
}

test('unreadable sign-in times show on the Insider Threat card', async ({ page, app }) => {
  await mkdir(SHOTS, { recursive: true })
  await signIn(page, app.baseURL)
  const collectorId = await page.evaluate(async (base) => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('wh.token')}`, 'Content-Type': 'application/json' }
    const made = await fetch(`${base}/api/collectors`, {
      method: 'POST', headers, body: JSON.stringify({ name: 'insider unreadable', kind: 'log', mode: 'docker' }),
    })
    const body = await made.json()
    await fetch(`${base}/api/ingest/logs`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${body.token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: 'no-stamp',
        lines: [
          { source: 'sshd', message: 'Accepted password for ada from 203.0.113.8' },
          { source: 'sshd', message: 'Accepted password for ben from 203.0.113.9' },
        ],
      }),
    })
    return body.collector.id
  }, app.baseURL)
  try {
    await page.goto(`${app.baseURL}/agents`)
    await expect(page.getByText(NOTE)).toBeVisible()
    await page.setViewportSize({ width: 1440, height: 900 })
    const card = page.locator('section.card', { hasText: 'Insider Threat' })
    await card.screenshot({ path: path.join(SHOTS, 'insider-1440.png') })
  } finally {
    await page.evaluate(async ({ base, id }) => {
      await fetch(`${base}/api/collectors/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}` },
      })
    }, { base: app.baseURL, id: collectorId })
    resetHealth(app.dataDir)
  }
})
