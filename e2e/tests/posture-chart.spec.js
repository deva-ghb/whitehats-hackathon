/** The posture chart plots days that were measured and leaves a gap for the rest. */

import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-035')

function sql(dataDir, source) {
  execFileSync('python3', ['-c', source, path.join(dataDir, 'whitehats.db')], { encoding: 'utf8' })
}

function plant(dataDir) {
  sql(dataDir, `
import sqlite3, sys
from datetime import date, timedelta
db = sqlite3.connect(sys.argv[1])
org, project = db.execute(
    "select o.id, p.id from organizations o join projects p on p.org_id = o.id and p.slug = 'main' "
    "where o.slug = 'acme'"
).fetchone()
today = date.today()
for ago, score in ((8, 500), (2, 460)):
    day = (today - timedelta(days=ago)).isoformat()
    db.execute("delete from posture_snapshots where project_id = ? and day = ?", (project, day))
    db.execute(
        "insert into posture_snapshots (org_id, project_id, day, score, breakdown) values (?, ?, ?, ?, ?)",
        (org, project, day, score, '{"score": %s}' % score),
    )
db.commit()
`)
}

function unplant(dataDir) {
  sql(dataDir, `
import sqlite3, sys
from datetime import date, timedelta
db = sqlite3.connect(sys.argv[1])
org = db.execute("select id from organizations where slug = 'acme'").fetchone()[0]
today = date.today()
for ago in (8, 2):
    day = (today - timedelta(days=ago)).isoformat()
    db.execute("delete from posture_snapshots where org_id = ? and day = ?", (org, day))
db.commit()
`)
}

test('posture chart shows points and leaves the unmeasured days empty', async ({ page, app }) => {
  plant(app.dataDir)
  try {
    await mkdir(SHOTS, { recursive: true })
    await signIn(page, app.baseURL)
    const chart = page.locator('svg.line-chart')
    await expect(chart).toBeVisible()
    const gaps = Number(await chart.getAttribute('data-gaps'))
    expect(gaps).toBeGreaterThan(0)
    expect(await chart.locator('circle').count()).toBeGreaterThanOrEqual(2)
    await expect(page.getByText('Only days with a reading are shown.')).toBeVisible()
    const card = page.locator('section.card', { has: page.getByRole('heading', { name: 'Posture over 30 days' }) })
    await page.setViewportSize({ width: 1440, height: 900 })
    await card.screenshot({ path: path.join(SHOTS, 'chart-1440.png') })
    await page.setViewportSize({ width: 390, height: 844 })
    await card.screenshot({ path: path.join(SHOTS, 'chart-390.png') })
  } finally {
    unplant(app.dataDir)
  }
})
