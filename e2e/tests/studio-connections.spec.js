/** Connections are nodes on the canvas (STUDIO.4): drag one in, set it up in the inspector, and what it finds lands inside it. */

import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { grantEditing } from '../connections.js'
import { startFakeAws } from '../fake-aws.js'
import { INSTALLATION, startFakeGithub } from '../fake-github.js'
import { WIDTHS, assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/STUDIO.4')
const REVOKED = 'ec2:DescribeInstances'

// The app runs against a fake AWS account (moto with IAM enforced). Nothing here reaches real AWS.
const test = base.extend({
  aws: [async ({}, use) => {
    const aws = await startFakeAws({ withFunction: true })
    await use(aws)
    await aws.stop()
  }, { scope: 'worker' }],
  github: [async ({}, use) => {
    const github = await startFakeGithub({ extraRepositories: ['fintech-shop'] })
    await use(github)
    await github.stop()
  }, { scope: 'worker' }],
  app: [async ({ aws, github }, use) => {
    // A throwaway value for the webhook secret, so the push listener is set up on this server; it is not a real secret.
    const started = await startApp({ ...aws.env, ...github.env, GITHUB_WEBHOOK_SECRET: 'e2e-only-webhook-secret-not-real' })
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.describe.configure({ mode: 'serial' })

const library = (page) => page.getByRole('complementary', { name: 'Node library' })
const inspector = (page) => page.getByRole('complementary', { name: 'Inspector' })
const estate = (page, name) => page.locator('.react-flow__node-estate').filter({ has: page.getByText(name, { exact: true }) })
const panel = (page, name) => inspector(page).getByRole('region', { name: `${name} connection` })

async function startClone(page, slug) {
  await page.goto(`${page.url().split('/p/')[0]}/studio`)
  const blank = page.getByRole('button', { name: 'Start a blank clone' })
  const more = page.getByRole('button', { name: 'New clone' })
  await expect(blank.or(more)).toBeVisible()
  await (await blank.isVisible() ? blank : more).click()
  await page.locator('#clone-name').fill(slug)
  await expect(async () => {
    await page.getByRole('button', { name: 'Create clone' }).click()
    await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible({ timeout: 1500 })
  }).toPass()
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
}

/** A real pointer drag from the library onto the canvas. */
async function dropType(page, type) {
  const canvas = await page.locator('.studio-canvas').boundingBox()
  await library(page).locator(`[data-type="${type}"]`).dragTo(page.locator('.studio-canvas'), { targetPosition: { x: canvas.width / 2, y: canvas.height / 2 } })
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
}

async function shots(page, name) {
  await mkdir(SHOTS, { recursive: true })
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`) })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('an AWS connection is dragged in, set up in the inspector, and what it finds lands inside it; a revoked permission is named on the node', async ({ page, app, aws }) => {
  test.setTimeout(240_000)
  const gate = attachGate(page)
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL)
  await startClone(page, 'connected-estate')

  // The library has a section of its own for connections.
  await expect(library(page).getByRole('heading', { name: 'Connections' })).toBeVisible()
  await expect(library(page).locator('[data-type="connection.aws"]')).toBeVisible()
  await dropType(page, 'connection.aws')
  await expect(page.locator('.react-flow__node-boundary').filter({ hasText: 'AWS connection' })).toBeVisible()
  await expect(panel(page, 'AWS')).toBeVisible()
  await expect(panel(page, 'AWS')).toContainText('Not connected')
  await assertAccessible(page)
  await shots(page, 'before-connecting')

  // Set it up: the role and external id from the template, and the regions. Then connect.
  const setup = panel(page, 'AWS')
  await setup.locator('#connection-role_arn').fill(aws.role_arn)
  await setup.locator('#connection-external_id').fill('e2e-external-id')
  await setup.locator('#connection-region').fill(aws.region)
  await setup.locator('#connection-regions').fill(`${aws.region}, us-east-1`)
  await setup.getByRole('button', { name: 'Connect' }).click()
  await expect(setup.getByTestId('permission-summary')).toContainText('All 19 permissions ok', { timeout: 30_000 })

  // What it found lands inside its node, as sub-nodes with their provenance.
  for (const name of ['fintech-receipts', 'orders', 'payment-jobs', 'admin-box', 'orders-api']) await expect(estate(page, name)).toBeVisible({ timeout: 30_000 })
  await expect(setup.getByTestId('connection-found')).toContainText('inside this node')
  // The view fits what arrived, so the person sees all of it.
  await page.waitForTimeout(800)
  const offScreen = await page.evaluate(() => {
    const frame = document.querySelector('.studio-canvas').getBoundingClientRect()
    return [...document.querySelectorAll('.react-flow__node-estate')].map((card) => ({ name: card.textContent.slice(0, 18), box: card.getBoundingClientRect() }))
      .filter(({ box }) => box.left < frame.left - 48 || box.top < frame.top - 48 || box.right > frame.right + 48 || box.bottom > frame.bottom + 48)
      .map(({ name, box }) => `${name} [${Math.round(box.left)},${Math.round(box.top)} to ${Math.round(box.right)},${Math.round(box.bottom)}] in canvas [${Math.round(frame.left)},${Math.round(frame.top)} to ${Math.round(frame.right)},${Math.round(frame.bottom)}]`)
  })
  expect(offScreen, 'resources that arrived but are off the screen').toEqual([])
  await shots(page, 'after-discovery')
  await estate(page, 'orders').click()
  await expect(inspector(page).getByText('Inside')).toBeVisible()
  await expect(inspector(page).getByText('discovered, from aws')).toBeVisible()
  await assertAccessible(page)

  // The saved clone holds the connector's name and nothing else of the connection: no role, no external id.
  const saved = await page.evaluate(async () => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}` }
    const list = await (await fetch(`/api/clones?project=${project}`, { headers })).json()
    const found = list.clones.find((clone) => clone.clone === 'connected-estate')
    return JSON.stringify((await (await fetch(`/api/clones/${found.id}?project=${project}`, { headers })).json()).document)
  })
  expect(saved).toContain('"connector":"aws"')
  expect(saved).not.toContain('e2e-external-id')
  expect(saved).not.toContain(aws.role_arn)

  // One permission is taken away: the node says which, in words, and the resource that needed it is marked.
  await aws.revoke(REVOKED)
  await page.locator('.react-flow__node-boundary').filter({ hasText: 'AWS connection' }).click({ position: { x: 20, y: 12 } })
  await panel(page, 'AWS').getByRole('button', { name: 'Check again' }).click()
  await expect(panel(page, 'AWS').getByTestId('permission-summary')).toContainText('1 of 19 permissions missing', { timeout: 20_000 })
  await expect(panel(page, 'AWS').getByRole('list', { name: 'Missing permissions' }).getByRole('listitem'))
    .toHaveText([`${REVOKED} is missing. WhiteHats needs it to read EC2 instance settings: size, address, instance profile.`])
  await expect(estate(page, 'admin-box').locator('.estate-alert')).toBeVisible()
  expect(await estate(page, 'admin-box').getAttribute('aria-label')).toContain('connector problem: AWS cannot read this any more')
  await estate(page, 'admin-box').click()
  await expect(inspector(page).getByText(`AWS cannot read this any more: ${REVOKED} is missing.`)).toBeVisible()
  await assertAccessible(page)
  await shots(page, 'after-revoking')
  gate.assertClean()
})


/** What installing the GitHub App leaves behind (the install itself happens on github.com, so it is recorded): a checked connection attached to the project. */
function installGithub(dataDir) {
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
`, path.join(dataDir, 'whitehats.db'), INSTALLATION])
}

const REPOS = ['deva-ghb/whitehats-proving-ground', 'deva-ghb/fintech-shop']

async function pick(page, trigger, name) {
  await page.locator(trigger).focus()
  await page.keyboard.press('Enter')
  const option = page.getByRole('option', { name, exact: true })
  await expect(option).toBeVisible()
  await option.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('listbox')).toHaveCount(0)
}

/** Open the Studio on the clone the first journey made, with nothing selected. */
async function openEstate(page, app) {
  await page.goto(`${app.baseURL}/studio`)
  const picker = page.getByRole('combobox', { name: 'Clone' })
  if (await picker.isVisible().catch(() => false)) {
    await picker.click()
    await page.getByRole('option', { name: 'connected-estate' }).click()
  }
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
}

test('a GitHub connection finds repositories, their pipelines and a listening webhook; narrowing removes what falls out; an edge is drawn from a function', async ({ page, app }) => {
  test.setTimeout(300_000)
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  await openEstate(page, app)
  await dropType(page, 'connection.github')
  await expect(panel(page, 'GitHub')).toContainText('Not connected')
  await expect(panel(page, 'GitHub').getByRole('button', { name: 'Install the GitHub App' })).toBeVisible()

  // The install itself happens on github.com, so it is recorded: what it leaves behind is a checked connection.
  installGithub(app.dataDir)
  await page.reload()
  await openEstate(page, app)
  await page.locator('.react-flow__node-boundary').filter({ hasText: 'GitHub connection' }).click({ position: { x: 20, y: 12 } })
  await panel(page, 'GitHub').getByRole('button', { name: 'Find resources' }).click()
  await expect(panel(page, 'GitHub').getByTestId('connection-found')).toContainText('inside this node', { timeout: 60_000 })

  // The repositories, their pipelines (workflows) and the listener GitHub calls on a push are inside the node.
  for (const repo of REPOS) {
    await expect(estate(page, repo)).toBeVisible()
    await expect(estate(page, `${repo} workflows`)).toBeVisible()
  }
  await expect(estate(page, 'GitHub push listener')).toBeVisible()
  await estate(page, 'GitHub push listener').click()
  await expect(inspector(page).getByText('discovered, from github')).toBeVisible()
  await page.waitForTimeout(1600)
  await shots(page, 'github-found')

  // An edge from the discovered function to the repository it deploys from.
  await estate(page, 'orders-api').focus()
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Draw a connection' }).focus()
  await page.keyboard.press('Enter')
  await pick(page, '#edge-target', REPOS[0])
  await expect(page.locator('#edge-type')).toContainText('Deploys from')
  await page.getByRole('button', { name: 'Add connection' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Deploys from' })).toBeVisible()

  // Narrow to one repository: the other, and its pipeline, go away.
  await page.locator('.react-flow__node-boundary').filter({ hasText: 'GitHub connection' }).click({ position: { x: 20, y: 12 } })
  const scope = panel(page, 'GitHub').getByRole('group', { name: 'Repositories this project reads' })
  await scope.getByLabel('Only the ones I choose').check()
  await scope.getByLabel(REPOS[1]).uncheck()
  await scope.getByRole('button', { name: 'Save what this project reads' }).click()
  await expect(estate(page, REPOS[1])).toHaveCount(0, { timeout: 30_000 })
  await expect(estate(page, `${REPOS[1]} workflows`)).toHaveCount(0)
  await expect(estate(page, REPOS[0])).toBeVisible()
  await expect(estate(page, 'GitHub push listener')).toBeVisible()
  // What a person drew stays: the edge from the function to the repository that is still read.
  await page.waitForTimeout(900)
  await estate(page, 'orders-api').click()
  await expect(inspector(page).getByText('Deploys from', { exact: false }).first()).toBeVisible()
  await assertAccessible(page)
  await shots(page, 'github-narrowed')
  gate.assertClean()
})

/** The id of a clone of this project by its name, read the way the app reads it. */
const cloneIdOf = (page, name) => page.evaluate(async (wanted) => {
  const token = localStorage.getItem('wh.token')
  const project = location.pathname.split('/')[2]
  const list = await (await fetch(`/api/clones?project=${project}`, { headers: { Authorization: `Bearer ${token}` } })).json()
  return list.clones.find((clone) => clone.clone === wanted)?.id
}, name)

test('a connector outside this version is placed with the keyboard alone and says Coming soon, with its setup switched off', async ({ page, app }) => {
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL)
  await openEstate(page, app)
  // The keyboard alone: focus the type in the library and press Enter.
  await library(page).locator('[data-type="connection.okta"]').focus()
  await page.keyboard.press('Enter')
  const okta = panel(page, 'Okta')
  await expect(okta).toBeVisible()
  await expect(okta.getByText('Coming soon', { exact: true })).toBeVisible()
  await expect(okta.getByText('Okta can be placed and named on the canvas, but it cannot be connected in this version.')).toBeVisible()
  await expect(okta.getByRole('button', { name: 'Connect' })).toHaveCount(0)
  await expect(okta.locator('input')).toHaveCount(0)
  // It can be named, like any node.
  await inspector(page).getByLabel('Name').fill('Staff sign-in')
  await inspector(page).getByLabel('Name').blur()
  await expect(page.locator('.react-flow__node-boundary').filter({ hasText: 'Staff sign-in' })).toBeVisible()
  // One node stands for a connection: placing it again selects the one there instead of adding another.
  await library(page).locator('[data-type="connection.okta"]').focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Okta connection is already on the canvas.')).toBeVisible()
  await expect(page.locator('.react-flow__node-boundary').filter({ hasText: 'Staff sign-in' })).toHaveCount(1)
  await assertAccessible(page)
})

test('someone who may not set a connection up or edit the clone sees it read only, and the server refuses what they try', async ({ page, app }) => {
  await signIn(page, app.baseURL, 'approver@acme.example')
  const clone = await cloneIdOf(page, 'connected-estate')
  await page.goto(`${app.baseURL}/studio?clone=${clone}&connection=aws`)
  const aws = panel(page, 'AWS')
  await expect(aws).toBeVisible()
  await expect(aws).toContainText('Setting this connection up needs the account owner or an admin.')
  await expect(aws.locator('input[type="text"], input[type="password"]')).toHaveCount(0)
  for (const button of ['Connect', 'Find resources', 'Check again', 'Disconnect']) await expect(aws.getByRole('button', { name: button })).toHaveCount(0)
  await expect(page.getByText('Read only.')).toBeVisible()
  await assertAccessible(page)
  const refused = await page.evaluate(async (id) => {
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const connector = (await (await fetch(`/api/connectors?project=${project}`, { headers })).json()).find((row) => row.key === 'aws')
    const status = async (path, method, body) => (await fetch(`${path}?project=${project}`, { method, headers, body: JSON.stringify(body) })).status
    return {
      setup: await status(`/api/connectors/${connector.id}`, 'PUT', { values: { role_arn: 'x', external_id: 'y', region: 'z' } }),
      scope: await status(`/api/connectors/${connector.id}/scope`, 'PUT', { scope: {} }),
      find: await status(`/api/connectors/${connector.id}/discover`, 'POST'),
      bring: await status(`/api/clones/${id}/connections/aws/bring-in`, 'POST'),
    }
  }, clone)
  expect(refused).toEqual({ setup: 403, scope: 403, find: 403, bring: 403 })
})

test('an old Connectors link opens the Studio with that connection selected, and says plainly when GitHub could not be linked', async ({ page, app }) => {
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/connectors`)
  await expect.poll(() => page.url()).toContain('/studio?')
  await expect(panel(page, 'AWS')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Connectors' })).toHaveCount(0)
  await page.goto(`${app.baseURL}/connectors?github=not-linked&reason=GitHub+did+not+send+back+an+installation.`)
  await expect(panel(page, 'GitHub')).toBeVisible()
  await expect(page.getByRole('alert').filter({ hasText: 'GitHub was not connected: GitHub did not send back an installation.' })).toBeVisible()
})

test('in the scene a connection is dragged in and its panel is the same, at every width', async ({ page, app }) => {
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL)
  await openEstate(page, app)
  await page.getByRole('button', { name: 'Scene view' }).click()
  await dropType(page, 'connection.jira')
  await expect(panel(page, 'Jira')).toBeVisible()
  await expect(panel(page, 'Jira').getByText('Coming soon', { exact: true })).toBeVisible()
  await assertAccessible(page)
  await shots(page, 'scene-connection')
})
