/** Role rules the server enforces, as a person meets them on screen (R-041). */

import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-041')
const CRITICAL_FIX = 'Fix uploads that can read any server file'

function sql(dataDir, statement) {
  execFileSync('python3', ['-c', `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute(sys.argv[2])
db.commit()
`, path.join(dataDir, 'whitehats.db'), statement])
}

async function callApi(page, method, url, body) {
  return page.evaluate(async ([m, u, b]) => {
    const res = await fetch(u, {
      method: m,
      headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}`, 'Content-Type': 'application/json' },
      body: b === undefined ? undefined : JSON.stringify(b),
    })
    return { status: res.status, body: await res.json() }
  }, [method, url, body])
}

// A dialog is shot as the viewport shows it; a page is shot whole, from the top, so the sticky bars stay in place.
async function evidence(page, name, { dialog = false } = {}) {
  await mkdir(SHOTS, { recursive: true })
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    if (!dialog) await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`), fullPage: !dialog })
    if (width === 390) {
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow).toBeLessThanOrEqual(1)
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('a project member is removed with a reason, and nobody changes their own role', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  const made = await callApi(page, 'POST', '/api/projects', { name: 'Rules desk', kind: 'empty' })
  expect(made.status).toBe(200)
  const slug = made.body.slug
  try {
    await page.goto(`${app.baseURL}/p/${slug}/settings`)
    await page.locator('#member-email').fill('analyst@acme.example')
    const inviteButton = page.getByRole('button', { name: 'Invite to this project' })
    await expect(inviteButton).toBeDisabled()  // a grant says why
    await page.locator('#member-reason').fill('Reviews the payments estate')
    await inviteButton.click()
    await expect(page.getByText('analyst@acme.example')).toBeVisible()
    await expect(page.getByText('Nobody changes their own role')).toBeVisible()

    await page.getByRole('button', { name: /^Remove .+ from this project$/ }).click()
    const dialog = page.getByRole('dialog')
    const confirm = dialog.getByRole('button', { name: 'Remove from project' })
    await expect(confirm).toBeDisabled()
    await dialog.locator('#remove-reason').fill('Moved to another team')
    await assertAccessible(page)
    await evidence(page, 'remove-member', { dialog: true })
    await confirm.click()
    await expect(page.getByText('Removed from this project')).toBeVisible()
    await expect(page.getByText('Removed from this project')).toHaveCount(0, { timeout: 10_000 })  // the toast clears
    await expect(page.getByText('analyst@acme.example')).toHaveCount(0)
    await assertAccessible(page)
    await evidence(page, 'project-people')
    gate.assertClean()
  } finally {
    await callApi(page, 'PATCH', `/api/projects/${slug}`, { archived: true })
  }
})

test('a critical fix in a two-approver project waits for a second, different approver', async ({ page, app }) => {
  const gate = attachGate(page)
  sql(app.dataDir, "UPDATE projects SET two_approver = 1 WHERE slug = 'main' AND org_id = (SELECT id FROM organizations WHERE slug = 'acme')")
  try {
    await signIn(page, app.baseURL)
    await page.goto(`${app.baseURL}/p/main/approvals`)
    const card = page.locator('section.card', { has: page.getByRole('heading', { name: CRITICAL_FIX }) })
    await card.getByRole('button', { name: 'Apply Fix' }).click()
    await expect(page.getByText('First approval recorded. A second Approver must decide.')).toBeVisible()
    await expect(card.getByText('You gave the first approval.')).toBeVisible()
    await expect(card.getByRole('button', { name: 'Apply Fix' })).toBeDisabled()
    await expect(page.getByText('First approval recorded. A second Approver must decide.')).toHaveCount(0, { timeout: 10_000 })
    await assertAccessible(page)
    await evidence(page, 'second-approver')
    gate.assertClean()
  } finally {
    sql(app.dataDir, "UPDATE projects SET two_approver = 0 WHERE slug = 'main'")
    sql(app.dataDir, `UPDATE remediations SET first_approved_by = NULL, first_approved_by_id = NULL, first_approved_at = NULL WHERE title = '${CRITICAL_FIX}'`)
  }
})

test('an archived project is read-only for the account owner, who sees it apart and can restore it (R-047)', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  const made = await callApi(page, 'POST', '/api/projects', { name: 'Old ledger', kind: 'empty' })
  expect(made.status).toBe(200)
  const slug = made.body.slug
  expect((await callApi(page, 'PATCH', `/api/projects/${slug}`, { archived: true })).status).toBe(200)

  await page.goto(`${app.baseURL}/`)
  const archived = page.getByRole('region', { name: 'Archived projects' })
  const card = archived.locator('.project-card', { has: page.getByRole('link', { name: 'Open Old ledger' }) })
  await expect(card).toBeVisible()
  await expect(card.getByText('Archived, read-only')).toBeVisible()
  await expect(page.locator('.project-grid').first().getByRole('link', { name: 'Open Old ledger' })).toHaveCount(0)
  await assertAccessible(page)
  await evidence(page, 'archived-projects')

  await card.getByRole('link', { name: 'Open Old ledger' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'This project is archived, so it is read-only.' })).toBeVisible()
  // The server says the same: reading works, changing does not.
  expect((await callApi(page, 'GET', `/api/projects/${slug}/settings?project=${slug}`)).status).toBe(200)
  // Sent outside the page, so the page's own requests are all the gate watches.
  const token = await page.evaluate(() => localStorage.getItem('wh.token'))
  const change = await page.request.patch(`${app.baseURL}/api/projects/${slug}/policy?project=${slug}`, {
    headers: { Authorization: `Bearer ${token}` }, data: { two_approver: true } })
  expect(change.status()).toBe(403)
  expect((await change.json()).detail).toContain('read-only')
  await assertAccessible(page)
  await evidence(page, 'archived-overview')

  await page.goto(`${app.baseURL}/`)
  await card.getByRole('button', { name: 'Restore Old ledger' }).click()
  await expect(card).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Open Old ledger' })).toBeVisible()
  gate.assertClean()
  // Leave it archived again, as the other tests in this file leave their projects.
  await callApi(page, 'PATCH', `/api/projects/${slug}`, { archived: true })
})
