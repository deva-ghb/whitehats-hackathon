/** Import an estate (PG.3): read a Terraform state into a clone, review each change, apply, and read it again. */

import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { assertAccessible, attachGate, shoot, tabTo } from '../gate.js'
import { signIn } from '../sign-in.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = path.resolve(HERE, '../screenshots/PG.3')
const STATE = path.resolve(HERE, '../../backend/tests/fixtures/terraform/shop.state.json')
// Its own server and database: the clones this journey makes must not become the clones other specs open.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})
test.describe.configure({ mode: 'serial' })

// The recorded shop state, plus one resource the node library has no type for, so one change is held.
async function stateFile(extra = true) {
  const state = JSON.parse(await readFile(STATE, 'utf8'))
  if (extra) {
    state.resources.push({ mode: 'managed', type: 'aws_kinesis_stream', name: 'clicks', instances: [{ attributes: { id: 'clicks', name: 'clicks' } }] })
    // A setting that only configures another resource: counted, not drawn, and its long name must not push the page wider.
    state.resources.push({ mode: 'managed', type: 'aws_s3_bucket_server_side_encryption_configuration', name: 'receipts', instances: [{ attributes: { id: 'receipts' } }] })
  }
  return { name: 'shop.tfstate', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(state)) }
}

async function pick(page, trigger, name) {
  await page.locator(trigger).focus()
  await page.keyboard.press('Enter')
  const option = page.getByRole('option', { name })
  await expect(option).toBeVisible()
  const search = page.getByRole('textbox', { name: 'Search options' })
  if (await search.count()) {  // a long list opens on its search box: filter it, then choose the one match
    await search.fill(name)
    await expect(page.getByRole('option')).toHaveCount(1)
    await option.click()
  } else {
    await option.focus()
    await page.keyboard.press('Enter')
  }
  await expect(page.getByRole('listbox')).toHaveCount(0)
}

async function snapshots(page, name) {
  await mkdir(SHOTS, { recursive: true })
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

async function quiet(page) {
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 })
}

test('an engineer imports a state into a new clone, decides each change, applies it, and a second read is empty', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio`)
  await expect(page.getByRole('heading', { name: 'No clone yet' })).toBeVisible()
  await page.getByRole('link', { name: 'Import an estate' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Import', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No import yet' })).toBeVisible()  // empty
  await expect(page.getByRole('button', { name: 'Read it and compare' })).toBeDisabled()  // no file chosen yet
  await assertAccessible(page)

  // A file that is not a state says so in plain words and changes nothing.
  await page.getByTestId('state-file').setInputFiles({ name: 'odd.json', mimeType: 'application/json', buffer: Buffer.from('{"version": 2, "resources": []}') })
  await page.locator('#import-name').fill('Imported shop')
  await page.getByRole('button', { name: 'Read it and compare' }).click()
  await expect(page.getByText('Terraform state version must be 3 or 4')).toBeVisible()
  await quiet(page)
  expect(gate.problems.filter((line) => line.includes('/api/imports'))).toHaveLength(1)  // the refusal is the only expected failure
  gate.problems.length = 0

  await page.getByTestId('state-file').setInputFiles(await stateFile())
  await expect(page.getByText('shop.tfstate, ')).toBeVisible()
  await shoot(page, 'import-start')
  await page.getByRole('button', { name: 'Read it and compare' }).click()

  await expect(page.getByRole('heading', { level: 2, name: 'Terraform state, shop.tfstate' })).toBeVisible()
  await expect(page.getByText('Waiting for your decisions')).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Needs a type (1)' })).toBeVisible()
  await expect(page.getByText('Nothing in it yet')).toHaveCount(2)  // nothing is in the clone yet, and nothing is accepted
  await expect(page.getByText('1 setting was read but is not drawn as a box')).toBeVisible()
  await page.getByText('1 setting was read').click()
  await expect(page.getByText('aws_s3_bucket_server_side_encryption_configuration')).toBeVisible()
  await assertAccessible(page)
  await quiet(page)
  await shoot(page, 'import-review')
  await snapshots(page, 'review-before')

  // Reject one and change your mind: the row moves between lists. Then reject it for good.
  await page.getByRole('button', { name: 'Reject admin-vm', exact: true }).click()
  await page.getByRole('tab', { name: 'Rejected (1)' }).click()
  await page.getByRole('button', { name: 'Undo the decision on admin-vm', exact: true }).click()
  await page.getByRole('tab', { name: 'To decide (' }).click()
  await page.getByRole('button', { name: 'Reject admin-vm', exact: true }).click()

  // One change by hand, then everything that is ready.
  await page.getByRole('button', { name: 'Accept shop-api', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Accepted (1)' })).toBeVisible()
  await page.getByRole('button', { name: 'that are ready' }).click()
  await expect(page.getByRole('button', { name: 'Accept all 0 that are ready' })).toBeDisabled()
  await expect(page.getByRole('tab', { name: 'To decide (0)' })).toBeVisible()

  // The held change cannot be accepted until a library type is chosen.
  await page.getByRole('tab', { name: 'Needs a type (1)' }).click()
  await expect(page.getByText('The node library has no type for this yet.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Accept clicks', exact: true })).toBeDisabled()
  await pick(page, '[id="type-node:n_aws_kinesis_stream_clicks"]', 'Amazon SQS queue')
  await expect(page.getByRole('button', { name: 'Accept clicks', exact: true })).toBeEnabled()
  await quiet(page)
  await shoot(page, 'import-held')
  await page.getByRole('button', { name: 'Accept clicks', exact: true }).click()
  await page.getByRole('tab', { name: 'Accepted (' }).click()
  await expect(page.getByRole('row', { name: 'clicks' }).getByText('Accepted')).toBeVisible()
  await expect(page.getByText('100% from import')).toHaveCount(1)  // after the accepted changes; the clone is still empty now

  await page.getByRole('button', { name: 'accepted changes' }).click()
  await expect(page.getByText('Applied as version 2 of imported-shop.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apply' })).toHaveCount(0)
  await quiet(page)
  await snapshots(page, 'review-applied')
  await assertAccessible(page)

  await page.getByRole('link', { name: 'Open it in the Studio to finish by hand' }).click()
  await expect(page.locator('.react-flow__node-estate').filter({ hasText: 'shop-api' }).first()).toBeVisible()
  await page.getByRole('link', { name: 'Import an estate' }).click()
  await expect(page.getByRole('link', { name: 'Terraform state, shop.tfstate' })).toBeVisible()
  await expect(page.getByText('Applied', { exact: true })).toBeVisible()

  // The same file again: nothing has changed, so no import is opened.
  await page.getByTestId('state-file').setInputFiles(await stateFile())
  await page.getByRole('button', { name: 'Read it and compare' }).click()
  await expect(page.getByText('Nothing has changed since the clone was last brought up to date.')).toBeVisible()
  await quiet(page)
  await shoot(page, 'import-history')
  gate.assertClean()
})

test('an applied import opens from the keyboard, and a later read from the keyboard finds nothing new', async ({ page, app }) => {
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio/import`)
  await page.getByRole('link', { name: 'Terraform state, shop.tfstate' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('This import was applied as version 2')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apply' })).toHaveCount(0)

  await page.getByRole('link', { name: 'All imports' }).click()
  await page.getByTestId('state-file').setInputFiles(await stateFile())
  await pick(page, '#import-clone', 'imported-shop')
  await tabTo(page, 'Read it and compare')
  await page.keyboard.press('Enter')
  await expect(page.getByText('Nothing has changed since the clone was last brought up to date.')).toBeVisible()
})

test('a person who may not edit the model reads an import but cannot start or decide one', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'ceo@acme.example')
  await page.goto(`${app.baseURL}/studio/import`)
  await expect(page.getByText('Your role can read imports but not start or decide them.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Read it and compare' })).toHaveCount(0)
  await page.getByRole('link', { name: 'Terraform state, shop.tfstate' }).click()
  await expect(page.getByText('This import was applied as version 2')).toBeVisible()
  for (const name of ['Accept', 'Reject', 'Apply', 'Discard']) await expect(page.getByRole('button', { name })).toHaveCount(0)
  gate.assertClean()
})
