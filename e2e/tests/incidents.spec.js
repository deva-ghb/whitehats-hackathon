/** Incidents are opened by hand from signals a person chooses. */

import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-021')
const HONEST = 'Incidents are opened by hand for now. Automatic correlation is coming.'
const TITLE = 'Opened by hand in the browser test'
const SIGNAL_TITLES = [
  'Hand opened signal one for the incident test',
  'Hand opened signal two for the incident test',
]

function sql(dataDir, source) {
  return execFileSync('python3', ['-c', source, path.join(dataDir, 'whitehats.db')], { encoding: 'utf8' }).trim()
}

function insertSignals(dataDir) {
  sql(dataDir, `
import sqlite3, sys
from datetime import datetime, timezone
db = sqlite3.connect(sys.argv[1])
org, project = db.execute(
    "select o.id, p.id from organizations o join projects p on p.org_id = o.id and p.slug = 'main' "
    "where o.slug = 'acme'"
).fetchone()
now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
titles = ${JSON.stringify(SIGNAL_TITLES)}
for title in titles:
    db.execute(
        "insert into signals (org_id, project_id, severity, title, detail, entity, resource, mitre, source, origin, "
        "created_at, node_key, fingerprint, branch, closed_reason) values (?, ?, 'high', ?, "
        "'Seen during the incident test.', '10.9.9.9', '', '', 'whitehat:network', 'sample', ?, '', '', '', '')",
        (org, project, title, now),
    )
db.commit()
`)
}

function removeHandOpened(dataDir) {
  sql(dataDir, `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys=ON')
titles = ${JSON.stringify(SIGNAL_TITLES)}
refs = [row[0] for row in db.execute("select ref from incidents where title = ?", (${JSON.stringify(TITLE)},))]
ids = [row[0] for row in db.execute(
    "select id from incidents where title = ?", (${JSON.stringify(TITLE)},))]
if ids:
    marks = ','.join('?' * len(ids))
    db.execute(f"delete from incident_events where incident_id in ({marks})", ids)
    db.execute(f"delete from signals where incident_id in ({marks})", ids)
    db.execute(f"delete from incidents where id in ({marks})", ids)
if refs:
    db.execute(
        f"delete from audit_log where action = 'opened incident' and target in ({','.join('?' * len(refs))})",
        refs,
    )
db.execute(f"delete from signals where title in ({','.join('?' * len(titles))})", titles)
db.commit()
`)
}

test('a person opens an incident from chosen signals', async ({ page, app }) => {
  await signIn(page, app.baseURL)
  await page.route('**/api/incidents?status=open*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: '[]',
  }))
  await page.goto(`${app.baseURL}/incidents`)
  await expect(page.getByRole('heading', { level: 3, name: 'No incidents' })).toBeVisible()
  await expect(page.getByText(HONEST)).toBeVisible()
  await expect(page.getByText('The swarm will open one')).toHaveCount(0)
  await page.unroute('**/api/incidents?status=open*')
  await page.reload()
  await expect(page.getByText('INC-2208')).toBeVisible()
  await assertAccessible(page)

  await mkdir(SHOTS, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'incidents-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, 'incidents-390.png'), fullPage: true })

  insertSignals(app.dataDir)
  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.getByRole('button', { name: 'Open an incident' }).click()
    const dialog = page.getByRole('dialog', { name: 'Open an incident' })
    await expect(dialog.getByText(HONEST)).toBeVisible()
    await expect(dialog.getByText('Every signal is already on an incident.')).toHaveCount(0)
    for (const title of SIGNAL_TITLES) {
      await dialog.getByRole('checkbox', { name: new RegExp(title) }).check()
    }
    await dialog.locator('#incident-title').fill(TITLE)
    await dialog.getByRole('button', { name: 'Open an incident' }).click()
    await expect(page.getByRole('heading', { level: 2, name: TITLE })).toBeVisible()
    await expect(page.getByText(/Opened by hand from 2 signal/)).toBeVisible()
    for (const title of SIGNAL_TITLES) await expect(page.getByText(title, { exact: true })).toBeVisible()
    await expect(page.getByText('Sample').first()).toBeVisible()
  } finally {
    removeHandOpened(app.dataDir)
  }
})
