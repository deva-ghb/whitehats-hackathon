/**
 * PG.3, the comparison half: the real proving-ground Terraform state is imported through the Studio's import screen into a new
 * clone, next to the clone P2.5 drew by hand, on a local server with no cloud access. Both are exported to test-results/pg3-compare
 * for scripts/compare_clones.py.
 *
 * The state file holds secret values and is never in the repository, so this test runs only when PG_STATE_FILE names it, and the
 * file is only ever handed to the import screen (the importer keeps secret values out of what it stores).
 *
 * After the state, the repository is scanned into the same clone (P5.2), the way a person would. By default the files come from a fake
 * GitHub that replays the recorded answers of the real repository. With PG_LIVE_GITHUB=1 the server reads the real repository through
 * the real WhiteHats GitHub App, read only: it needs GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH and PG_GITHUB_INSTALLATION_ID.
 */

import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { startApp, test as base } from '../fixtures.js'
import { INSTALLATION, startFakeGithub } from '../fake-github.js'
import { assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = path.resolve(HERE, '../screenshots/PG.3')
const OUT = path.resolve(HERE, '../test-results/pg3-compare')
const HAND_FILE = path.resolve(HERE, '../screenshots/P2.5/proving-ground-by-hand.clone.json')
const STATE_FILE = process.env.PG_STATE_FILE
const IMPORTED = 'proving-ground-imported'

const LIVE = process.env.PG_LIVE_GITHUB === '1'
const test = base.extend({
  github: [async ({}, use) => {
    const github = LIVE ? null : await startFakeGithub()
    await use(github)
    await github?.stop()
  }, { scope: 'worker' }],
  app: [async ({ github }, use) => {
    const live = { GITHUB_APP_ID: process.env.GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH: process.env.GITHUB_APP_PRIVATE_KEY_PATH }
    const started = await startApp(LIVE ? live : github.env)
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

/** What connecting GitHub leaves behind: a checked connection attached to the project, with no narrowing. */
function connectGithub(dataDir, installation) {
  execFileSync('python3', ['-c', `
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
org = db.execute("SELECT id FROM organizations WHERE slug = 'acme'").fetchone()[0]
project = db.execute("SELECT id FROM projects WHERE org_id = ? AND slug = 'main'", (org,)).fetchone()[0]
connector = db.execute("SELECT id FROM connectors WHERE org_id = ? AND key = 'github'", (org,)).fetchone()[0]
db.execute("UPDATE connectors SET status = 'checked', config = ? WHERE id = ?", (json.dumps({"installation_id": sys.argv[2], "account": "deva-ghb"}), connector))
db.execute("DELETE FROM project_connections WHERE project_id = ? AND connector_id = ?", (project, connector))
db.execute("INSERT INTO project_connections (org_id, project_id, connector_id, scope, attached_by, attached_at) VALUES (?, ?, ?, '{}', 'test', datetime('now'))", (org, project, connector))
db.commit()
`, path.join(dataDir, 'whitehats.db'), installation])
}
test.skip(!STATE_FILE, 'Set PG_STATE_FILE to the real proving-ground state (git-ignored, holds secret values) to run this comparison.')

async function exportClone(page, name) {
  return page.evaluate(async (slug) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}` }
    const list = await (await fetch(`/api/clones?project=${project}`, { headers })).json()
    const found = list.clones.find((clone) => clone.clone === slug)
    return found ? (await fetch(`/api/clones/${found.id}/export?project=${project}`, { headers })).json() : null
  }, name)
}

test('the real state is imported into a new clone with no cloud call, and both clones are exported for the comparison', async ({ page, app }) => {
  test.setTimeout(360_000)
  await mkdir(SHOTS, { recursive: true })
  await mkdir(OUT, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio`)

  // The hand-built clone is brought in the way any exported clone is: Import a clone.
  await page.getByTestId('clone-file').setInputFiles(HAND_FILE)
  await expect(page.getByRole('toolbar', { name: 'Clone' }).getByText('proving-ground-by-hand')).toBeVisible()

  // Import an estate: the real state, into a new clone, read and compared before anything is saved.
  await page.getByRole('link', { name: 'Import an estate' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Import', exact: true })).toBeVisible()
  await page.getByTestId('state-file').setInputFiles(STATE_FILE)
  await page.getByRole('combobox', { name: 'Into this clone' }).click()
  await page.getByRole('option').filter({ has: page.getByText('A new clone', { exact: true }) }).click()
  await page.getByLabel('Name of the new clone').fill(IMPORTED)
  await page.getByRole('button', { name: 'Read it and compare' }).click()
  await expect(page.getByText('Waiting for your decisions')).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Needs a type (3)' })).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'compare-review-1440.png'), fullPage: true })
  await assertAccessible(page)

  // The three that have no library type are rejected (nothing to choose); everything else is accepted.
  await page.getByRole('tab', { name: 'Needs a type (3)' }).click()
  for (let left = 3; left > 0; left -= 1) {
    await page.getByRole('button', { name: 'Reject ' }).first().click()
    await expect(page.getByRole('tab', { name: `Needs a type (${left - 1})` })).toBeVisible()
  }
  await page.getByRole('tab', { name: 'To decide (' }).click()
  await page.getByRole('button', { name: 'that are ready' }).click()
  await expect(page.getByRole('tab', { name: 'To decide (0)' })).toBeVisible()
  await page.getByRole('button', { name: 'accepted changes' }).click()
  await expect(page.getByText('Applied as version 2 of')).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'compare-applied-1440.png'), fullPage: true })

  // Then the repository, into the same clone: what the Terraform state cannot say (the pipeline, who deploys from where, the buckets).
  connectGithub(app.dataDir, LIVE ? process.env.PG_GITHUB_INSTALLATION_ID : INSTALLATION)
  await page.getByRole('link', { name: 'All imports' }).click()
  await page.getByRole('tab', { name: 'A repository' }).click()
  await expect(page.getByText('This only reads. Nothing is written to GitHub')).toBeVisible()
  await page.getByRole('combobox', { name: 'Into this clone' }).click()
  await page.getByRole('option', { name: IMPORTED }).click()
  await page.getByRole('button', { name: 'Read it and compare' }).click()
  await expect(page.getByText('Waiting for your decisions')).toBeVisible({ timeout: 120_000 })
  await page.screenshot({ path: path.join(SHOTS, 'compare-repository-review-1440.png'), fullPage: true })
  await page.getByRole('button', { name: 'that are ready' }).click()
  await page.getByRole('button', { name: 'accepted changes' }).click()
  await expect(page.getByText('Applied as version 3 of')).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'compare-repository-applied-1440.png'), fullPage: true })

  // The state again: the buckets exist in the clone now, so the connections that were waiting for them are made.
  await page.getByRole('link', { name: 'All imports' }).click()
  await page.getByTestId('state-file').setInputFiles(STATE_FILE)
  await page.getByRole('combobox', { name: 'Into this clone' }).click()
  await page.getByRole('option', { name: IMPORTED }).click()
  await page.getByRole('button', { name: 'Read it and compare' }).click()
  await expect(page.getByText('Waiting for your decisions')).toBeVisible()
  await page.getByRole('button', { name: 'that are ready' }).click()
  await page.getByRole('button', { name: 'accepted changes' }).click()
  await expect(page.getByText('Applied as version 4 of')).toBeVisible()

  await page.getByRole('link', { name: 'Open it in the Studio to finish by hand' }).click()
  await expect(page.getByRole('toolbar', { name: 'Clone' })).toBeVisible()
  await expect(page.locator('.react-flow__node-estate').first()).toBeVisible()
  await page.getByRole('button', { name: 'Fit to screen' }).click()
  await page.screenshot({ path: path.join(SHOTS, 'compare-imported-studio-1440.png') })

  const hand = await exportClone(page, 'proving-ground-by-hand')
  const imported = await exportClone(page, IMPORTED)
  expect(hand, 'the hand-built clone is in the project').not.toBeNull()
  expect(imported, 'the imported clone is in the project').not.toBeNull()
  await writeFile(path.join(OUT, 'hand.clone.json'), `${JSON.stringify(hand, null, 2)}\n`)
  await writeFile(path.join(OUT, 'imported.clone.json'), `${JSON.stringify(imported, null, 2)}\n`)
  gate.assertClean()
})
