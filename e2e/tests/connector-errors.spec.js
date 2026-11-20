/** A cloud log source that stopped reading shows the error once and offers to try again (R-039). Its node on the canvas says it (STUDIO.4). */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { ensureConnections, grantEditing, openConnection } from '../connections.js'
import { assertAccessible, attachGate, shoot } from '../gate.js'
import { signIn } from '../sign-in.js'

// Its own app: the owner is made a project admin and a clone is made, neither of which the other specs on the shared app may see.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

const REASON = 'Reading cloudtrail failed: An error occurred (ResourceNotFoundException) when calling the FilterLogEvents operation: The specified log group does not exist.'

function setCloudWatch(dataDir, state) {
  execFileSync('python3', ['-c', `
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
state = json.loads(sys.argv[2])
db.execute("UPDATE connectors SET status = ?, last_check = ?, state = ?, last_sync_at = ?, config = ? "
           "WHERE key = 'cloudwatch' AND org_id = (SELECT id FROM organizations WHERE slug = 'acme')",
           (state["status"], state["last_check"], json.dumps(state["state"]), state["last_sync_at"],
            json.dumps(state["config"])))
db.commit()
`, path.join(dataDir, 'whitehats.db'), JSON.stringify(state)])
}

const FAILED = {
  status: 'error', last_check: REASON, last_sync_at: '2026-10-23 09:00:00',
  state: { total: 42, last_count: 0, last_error: REASON }, config: { region: 'ap-south-1', log_groups: 'cloudtrail' },
}
const AVAILABLE = { status: 'available', last_check: '', last_sync_at: null, state: {}, config: {} }

test('a source that failed after reading shows the reason once and offers to try again', async ({ page, app }) => {
  setCloudWatch(app.dataDir, FAILED)
  try {
    const gate = attachGate(page)
    grantEditing(app.dataDir)
    await signIn(page, app.baseURL)
    const card = await openConnection(page, app.baseURL, await ensureConnections(page, ['cloudwatch']), 'cloudwatch')
    await expect(card.getByText('Stopped reading', { exact: true })).toBeVisible()
    await expect(card.getByText(REASON)).toHaveCount(1)
    await expect(card.getByText('Last good read')).toContainText('42 items')
    await expect(card.getByRole('button', { name: 'Try again' })).toBeVisible()
    await assertAccessible(page)
    // Reachable by keyboard: from the first control of the panel, a few Tabs reach it.
    await card.locator('summary').first().focus()
    let reached = false
    for (let step = 0; step < 12 && !reached; step += 1) {
      await page.keyboard.press('Tab')
      reached = (await page.evaluate(() => document.activeElement?.textContent || '')).includes('Try again')
    }
    expect(reached, 'Tab reaches Try again').toBe(true)
    await shoot(page, 'connector-error', { mask: [card.getByText('Last good read')] })
    gate.assertClean()
  } finally {
    setCloudWatch(app.dataDir, AVAILABLE)
  }
})

test('people who cannot manage connections are not offered a read they would be refused', async ({ page, app }) => {
  setCloudWatch(app.dataDir, FAILED)
  try {
    await signIn(page, app.baseURL, 'engineer@acme.example')
    const card = await openConnection(page, app.baseURL, await ensureConnections(page, ['cloudwatch']), 'cloudwatch')
    await expect(card.getByText(REASON)).toHaveCount(1)
    await expect(card.getByRole('button', { name: 'Try again' })).toHaveCount(0)
  } finally {
    setCloudWatch(app.dataDir, AVAILABLE)
  }
})

test('a source whose first check failed says so and is not offered a read', async ({ page, app }) => {
  setCloudWatch(app.dataDir, { ...FAILED, last_sync_at: null, state: {}, last_check: 'AWS sign-in failed: AccessDenied' })
  try {
    grantEditing(app.dataDir)
    await signIn(page, app.baseURL)
    const card = await openConnection(page, app.baseURL, await ensureConnections(page, ['cloudwatch']), 'cloudwatch')
    await expect(card.getByText('Check failed', { exact: true })).toBeVisible()
    await expect(card.getByText('AWS sign-in failed: AccessDenied')).toBeVisible()
    await expect(card.getByRole('button', { name: 'Try again' })).toHaveCount(0)
    await expect(card.getByText('Setup', { exact: true })).toBeVisible()  // the setup is there to change
  } finally {
    setCloudWatch(app.dataDir, AVAILABLE)
  }
})
