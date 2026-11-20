/** Apply Fix on a fix that touches two services and Terraform: three ordered pull requests (P8.3), from a recorded run. */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { assertAccessible, attachGate, shoot, tabTo } from '../gate.js'
import { startRecordedSplitFactory } from '../recorded-split-factory.js'
import { signIn } from '../sign-in.js'

const test = base.extend({
  engine: [async ({}, use) => {
    const engine = await startRecordedSplitFactory()
    await use(engine)
    await engine.stop()
  }, { scope: 'worker' }],
  app: [async ({ engine }, use) => {
    const started = await startApp(engine.env)
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

const REF = 'INC-9703'
const FIX = 'Log every error path with its request id'
const FINDING = 'Errors are not logged with a request id'

/** An incident and a pending Fix Factory fix of their own, so no other test changes what this one shows. */
function seed(dataDir, remove = false) {
  execFileSync('python3', ['-c', `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
org, project = db.execute(
    "select o.id, p.id from organizations o join projects p on p.org_id = o.id and p.slug = 'main' "
    "where o.slug = 'acme'").fetchone()
old = db.execute("select id from incidents where ref = ?", (sys.argv[3],)).fetchone()
if old:
    db.execute("delete from remediations where incident_id = ?", (old[0],))
    db.execute("delete from incident_events where incident_id = ?", (old[0],))
    db.execute("delete from incidents where id = ?", (old[0],))
if sys.argv[2] == 'add':
    cur = db.execute("insert into incidents (org_id, project_id, ref, title, summary, business_impact, owner, "
                     "severity, status, origin, created_at) values (?, ?, ?, 'Errors go unlogged in two services', "
                     "'Seeded by the browser test.', 'None, this is a test.', '', 'high', 'contained', 'live', "
                     "datetime('now'))", (org, project, sys.argv[3]))
    db.execute("insert into remediations (org_id, project_id, incident_id, title, target, action, why, "
               "destination, factory_intent, risk, simulated_impact, status, result, origin, created_at) "
               "values (?, ?, ?, ?, 'shop-api, payments-worker, log groups', "
               "'Log every error path with its request id in both services, and keep logs for 30 days.', "
               "'Errors in both services leave no trace.', 'Fix Factory', 'WH-INT-LOG-1', 'high', '', 'pending', '', "
               "'live', datetime('now'))", (org, project, cur.lastrowid, sys.argv[4]))
db.commit()
`, path.join(dataDir, 'whitehats.db'), remove ? 'remove' : 'add', REF, FIX])
}

function decidedRow(page) {
  return page.getByRole('row', { name: FIX })
}

async function openRun(page, baseURL) {
  await page.goto(`${baseURL}/fix-factory`)
  await page.locator('.list-item', { hasText: FINDING }).click()
}

test('Apply Fix opens three ordered pull requests whose CI passed, and merging them verifies the fix', async ({ page, app, engine }) => {
  const gate = attachGate(page)
  engine.playNext('opened')
  seed(app.dataDir)
  await signIn(page, app.baseURL)

  // Apply Fix by keyboard alone.
  await page.goto(`${app.baseURL}/approvals`)
  const card = page.locator('.card', { has: page.getByRole('heading', { name: FIX }) })
  await tabTo(page, card.getByRole('button', { name: 'Apply Fix' }))
  await page.keyboard.press('Enter')
  await expect(page.getByText('Sent to Fix Factory to be proven')).toBeVisible()

  // While the engine works, the fix claims only that it was sent.
  await expect(decidedRow(page)).toContainText('Sent to Fix Factory as run WH-INT-LOG-1-')
  await expect(decidedRow(page)).not.toContainText('pull request')

  engine.advance('opened')
  await page.reload()
  const result = decidedRow(page)
  await expect(result).toContainText('opened 3 pull requests, and CI passed on each. Merge them in this order on GitHub:')
  await expect(result).toContainText('1. #1 shop-api:')
  await expect(result).toContainText('2. #2 payments-worker:')
  await expect(result).toContainText('3. #3 infrastructure:')
  await expect(result.getByRole('link', { name: 'https://github.example/acme/shop/pull/3' }))
    .toHaveAttribute('href', 'https://github.example/acme/shop/pull/3')
  await shoot(page, 'split-fix-approvals', { mask: [page.locator('.num')] })

  await openRun(page, app.baseURL)
  const panel = page.locator('.card', { has: page.getByRole('heading', { name: 'Pull requests, in merge order' }) })
  await expect(panel.locator('.pill', { hasText: 'Awaiting merge' })).toBeVisible()
  const table = panel.getByRole('list', { name: 'Pull requests, in merge order' })
  const rows = table.getByRole('listitem')
  await expect(rows).toHaveCount(3)
  await expect(rows.nth(0)).toContainText('1 of 3shop-api#1')
  await expect(rows.nth(1)).toContainText('2 of 3payments-worker#2')
  await expect(rows.nth(2)).toContainText('3 of 3infrastructure#3')
  await expect(rows.nth(2)).toContainText('terraform/main.tf')
  await expect(table.getByText('CI passed')).toHaveCount(3)
  await expect(table.getByRole('link', { name: '#2' })).toHaveAttribute('href', 'https://github.example/acme/shop/pull/2')
  await expect(panel.getByText('A person merges them, in this order, on GitHub.')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Approve and merge' })).toHaveCount(0)
  await assertAccessible(page)
  await shoot(page, 'split-fix-run')

  // A person merges all three on GitHub; the engine records it and the fix is verified.
  engine.advance('merged')
  await page.goto(`${app.baseURL}/incidents`)
  await page.getByRole('row', { name: REF }).click()
  const fix = page.locator('.list-item', { hasText: FIX })
  await expect(fix).toContainText('verified')
  await expect(fix).toContainText('Merged on GitHub: #1 (shop-api), #2 (payments-worker), #3 (infrastructure).')
  await expect(page.getByText(`Fix Factory opened 3 pull requests: ${FIX}`)).toBeVisible()
  await expect(page.getByText(`Fix Factory proved and delivered: ${FIX}`)).toBeVisible()
  gate.assertClean()
  seed(app.dataDir, true)
})

test('CI that fails on one pull request is shown as failed, never as a proven fix', async ({ page, app, engine }) => {
  const gate = attachGate(page)
  engine.playNext('ci_failed')
  seed(app.dataDir)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/approvals`)
  const card = page.locator('.card', { has: page.getByRole('heading', { name: FIX }) })
  await card.getByRole('button', { name: 'Apply Fix' }).click()
  await expect(page.getByText('Sent to Fix Factory to be proven')).toBeVisible()
  engine.advance('ci_failed')

  await openRun(page, app.baseURL)
  const panel = page.locator('.card', { has: page.getByRole('heading', { name: 'Pull requests, in merge order' }) })
  await expect(panel.locator('.pill', { hasText: 'CI failed · to a person' })).toBeVisible()
  const rows = panel.getByRole('list', { name: 'Pull requests, in merge order' }).getByRole('listitem')
  await expect(rows.nth(1)).toContainText('CI failed')
  await expect(rows.nth(0)).toContainText('CI passed')
  await expect(panel.getByText('A person merges them')).toHaveCount(0)
  await assertAccessible(page)
  await shoot(page, 'split-fix-ci-failed')

  await page.goto(`${app.baseURL}/approvals`)
  await expect(decidedRow(page)).toContainText('did not fix this: CI did not pass on its pull requests.')
  await expect(decidedRow(page)).not.toContainText('CI passed')
  gate.assertClean()
  seed(app.dataDir, true)
})
