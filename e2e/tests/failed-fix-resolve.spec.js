/** A failed fix does not block Resolve; the screen warns and the timeline records it (R-045). */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, shoot } from '../gate.js'
import { signIn } from '../sign-in.js'

const SQL_FIX = 'Fix SQL injection in user lookup'

const REF = 'INC-9702'

/** An incident of its own, so no other test's notes or fixes change what this one shows. */
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
                     "severity, status, origin, created_at) values (?, ?, ?, 'Failed fix browser test', "
                     "'Seeded by the browser test.', 'None, this is a test.', '', 'high', 'investigating', 'live', "
                     "datetime('now'))", (org, project, sys.argv[3]))
    db.execute("insert into remediations (org_id, project_id, incident_id, title, target, action, why, "
               "destination, risk, simulated_impact, status, result, origin, created_at) "
               "values (?, ?, ?, ?, 'customer-api', 'Parameterise the query.', '', 'Fix Factory', 'medium', '', "
               "'failed', '', 'live', datetime('now'))", (org, project, cur.lastrowid, sys.argv[4]))
db.commit()
`, path.join(dataDir, 'whitehats.db'), remove ? 'remove' : 'add', REF, SQL_FIX])
}

test('an incident can be resolved after its automated fix failed, with a warning on the record', async ({ page, app }) => {
  const gate = attachGate(page)
  seed(app.dataDir)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/incidents`)
  await page.getByRole('row', { name: REF }).click()
  await expect(page.getByRole('heading', { name: 'Proposed fixes' })).toBeVisible()

  await page.locator('#change-status').click()
  await expect(page.locator('#incident-status')).toContainText('Fixed, please verify')
  const warning = page.locator('#failed-fix-warning')
  await expect(warning).toContainText('The automated fix failed')
  await expect(warning).toContainText(SQL_FIX)
  await page.locator('#status-note').fill('Fixed by hand: the lookup now uses a parameter')
  await shoot(page, 'failed-fix-resolve-warning')
  await assertAccessible(page)
  await page.getByRole('button', { name: 'Update status' }).click()

  await expect(page.getByText(`Warning: called fixed by a person although the automated fix failed: ${SQL_FIX}`)).toBeVisible()
  // WhiteHats checks again (FD.2): with nothing it can check on this incident, it resolves on the person's word, saying so.
  await expect(page.locator('.page-head')).toContainText('resolved', { timeout: 45_000 })
  gate.assertClean()
  seed(app.dataDir, true)
})
