/** Import an estate from a repository (P5.2): the files of the proving-ground repository, read through a fake GitHub that replays real answers. */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { INSTALLATION, startFakeGithub } from '../fake-github.js'
import { assertAccessible, attachGate, shoot, tabTo } from '../gate.js'
import { signIn } from '../sign-in.js'

const REPO = 'deva-ghb/whitehats-proving-ground'
// Its own server and database, and a fake GitHub: no request in this journey reaches github.com.
const test = base.extend({
  github: [async ({}, use) => {
    const github = await startFakeGithub()
    await use(github)
    await github.stop()
  }, { scope: 'worker' }],
  app: [async ({ github }, use) => {
    const started = await startApp(github.env)
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.describe.configure({ mode: 'serial' })

/** What connecting GitHub on the Connectors screen leaves behind: a checked connection, attached to the project with no narrowing. */
function connectGithub(dataDir) {
  execFileSync('python3', ['-c', `
import json, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
org = db.execute("SELECT id FROM organizations WHERE slug = 'acme'").fetchone()[0]
project = db.execute("SELECT id FROM projects WHERE org_id = ? AND slug = 'main'", (org,)).fetchone()[0]
connector = db.execute("SELECT id FROM connectors WHERE org_id = ? AND key = 'github'", (org,)).fetchone()[0]
db.execute("UPDATE connectors SET status = 'checked', config = ? WHERE id = ?",
           (json.dumps({"installation_id": sys.argv[2], "account": "deva-ghb"}), connector))
db.execute("DELETE FROM project_connections WHERE project_id = ? AND connector_id = ?", (project, connector))
db.execute("INSERT INTO project_connections (org_id, project_id, connector_id, scope, attached_by, attached_at) "
           "VALUES (?, ?, ?, '{}', 'test', datetime('now'))", (org, project, connector))
db.commit()
`, path.join(dataDir, 'whitehats.db'), INSTALLATION])
}

async function quiet(page) {
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
}

test('a repository is read into a new clone: what is missing is said first, a refusal is plain, and the review is applied', async ({ page, app, github }) => {
  test.setTimeout(180_000)
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio/import`)
  await page.getByRole('tab', { name: 'A repository' }).click()

  // Nothing is connected yet: the screen says so, in words, and the read cannot start.
  await expect(page.getByText('GitHub is not connected yet. Install the WhiteHats GitHub App on the repositories, then read them.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Read it and compare' })).toBeDisabled()
  await assertAccessible(page)
  await shoot(page, 'repository-not-connected')

  // Connected, and ready: the source says what it reads and that it only reads.
  connectGithub(app.dataDir)
  await page.reload()
  await page.getByRole('tab', { name: 'A repository' }).click()
  await expect(page.getByText('Reads every repository the WhiteHats GitHub App can see.')).toBeVisible()
  await expect(page.getByText('This only reads. Nothing is written to GitHub')).toBeVisible()
  await page.getByRole('combobox', { name: 'Into this clone' }).click()
  await page.getByRole('option').filter({ has: page.getByText('A new clone', { exact: true }) }).click()
  await page.getByLabel('Name of the new clone').fill('Scanned proving ground')
  await assertAccessible(page)
  await shoot(page, 'repository-ready')

  // GitHub turns the App away: the answer is a sentence, and no clone is started.
  github.refuseSignIn()
  await page.getByRole('button', { name: 'Read it and compare' }).click()
  await expect(page.getByText("GitHub denied the WhiteHats App's sign-in (401: Bad credentials)")).toBeVisible()
  await quiet(page)
  expect(gate.problems.filter((line) => line.includes('/api/imports'))).toHaveLength(1)  // the refusal is the only expected failure
  gate.problems.length = 0
  github.allowSignIn()

  await page.getByRole('button', { name: 'Read it and compare' }).click()
  await expect(page.getByRole('heading', { level: 2, name: `What ${REPO} holds` })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText('Waiting for your decisions')).toBeVisible()
  await expect(page.getByRole('tab', { name: 'To decide (20)' })).toBeVisible()  // 14 things and 6 connections
  await expect(page.getByRole('tab', { name: 'Needs a type (0)' })).toBeVisible()  // every type is one the library has
  await expect(page.getByText('2 things were read but are not drawn')).toBeVisible()
  await page.getByText('2 things were read but are not drawn').click()
  await expect(page.getByText('python project customer-loader, which nothing deploys')).toBeVisible()
  await expect(page.getByText(`${REPO}: module network_addon is off by default`)).toBeVisible()
  await expect(page.getByText('shop-api deploys from')).toHaveCount(1)
  await assertAccessible(page)
  await quiet(page)
  await shoot(page, 'repository-review')

  await page.getByRole('button', { name: 'that are ready' }).click()
  await expect(page.getByRole('tab', { name: 'To decide (0)' })).toBeVisible()
  await page.getByRole('button', { name: 'accepted changes' }).click()
  await expect(page.getByText('Applied as version 2 of scanned-proving-ground.')).toBeVisible()
  await quiet(page)
  await shoot(page, 'repository-applied')

  await page.getByRole('link', { name: 'Open it in the Studio to finish by hand' }).click()
  for (const name of ['shop-api', 'payments-worker', 'payment-jobs']) {
    await expect(page.locator('.react-flow__node-estate').filter({ hasText: name }).first()).toBeVisible()
  }
  gate.assertClean()
  // The scan only read: every request to GitHub was a GET, except the App's sign-in, which changes nothing.
  expect(github.requests.filter((line) => !line.startsWith('GET ') && !line.includes('/access_tokens'))).toEqual([])
})

test('a later read from the keyboard finds nothing new', async ({ page, app }) => {
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio/import`)
  await page.getByRole('link', { name: `What ${REPO} holds` }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('This import was applied as version 2')).toBeVisible()
  await page.getByRole('link', { name: 'All imports' }).click()
  await page.getByRole('tab', { name: 'A repository' }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('combobox', { name: 'Into this clone' }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('option', { name: 'scanned-proving-ground' }).focus()
  await page.keyboard.press('Enter')
  await tabTo(page, 'Read it and compare')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Nothing has changed since the clone was last brought up to date.')).toBeVisible({ timeout: 60_000 })
})
