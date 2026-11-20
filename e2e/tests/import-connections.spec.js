/** Import an estate from what a connection found (PG.3): connect a fake AWS account, find its resources, review them into a clone. */

import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { startFakeAws } from '../fake-aws.js'
import { ensureConnections, grantEditing, openConnection } from '../connections.js'
import { assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

// The app runs against a fake AWS account (moto with IAM enforced). Nothing here reaches real AWS.
const test = base.extend({
  aws: [async ({}, use) => {
    const aws = await startFakeAws()
    await use(aws)
    await aws.stop()
  }, { scope: 'worker' }],
  app: [async ({ aws }, use) => {
    const started = await startApp(aws.env)
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

test('what a connection found is read into a new clone, reviewed, and applied', async ({ page, app, aws }) => {
  test.setTimeout(240_000)
  const gate = attachGate(page)
  grantEditing(app.dataDir)
  await signIn(page, app.baseURL, 'engineer@acme.example')

  // Before anything is found, the source says what to do.
  await page.goto(`${app.baseURL}/studio/import`)
  await page.getByRole('tab', { name: 'What your connections found' }).click()
  await expect(page.getByText('Nothing found yet.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Read it and compare' })).toBeDisabled()

  // The owner connects AWS on its node on the canvas: connecting also finds what the account holds.
  await page.getByRole('button', { name: 'Sign out' }).click()
  await signIn(page, app.baseURL)
  const card = await openConnection(page, app.baseURL, await ensureConnections(page, ['aws']), 'aws')
  await card.locator('#connection-role_arn').fill(aws.role_arn)
  await card.locator('#connection-external_id').fill('e2e-external-id')
  await card.locator('#connection-region').fill(aws.region)
  await card.getByRole('button', { name: 'Connect' }).click()
  await expect(card.getByTestId('permission-summary')).toContainText('All 19 permissions ok', { timeout: 30_000 })
  await expect(card.getByText('Found 8 resources')).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: 'Sign out' }).click()
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio/import`)
  await page.getByRole('tab', { name: 'What your connections found' }).click()
  await expect(page.getByText('AWS found 8 resources')).toBeVisible()
  await page.getByRole('combobox', { name: 'Into this clone' }).click()
  await page.getByRole('option').filter({ has: page.getByText('A new clone', { exact: true }) }).click()
  await page.locator('#import-name').fill('Found by AWS')
  await assertAccessible(page)
  await page.getByRole('button', { name: 'Read it and compare' }).click()

  await expect(page.getByRole('heading', { level: 2, name: 'What AWS found' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Needs a type (1)' })).toBeVisible()  // the security group has no library type
  await page.getByRole('tab', { name: 'Needs a type (1)' }).click()
  await page.getByRole('button', { name: 'Reject', exact: false }).first().click()
  await page.getByRole('tab', { name: 'To decide (' }).click()
  await page.getByRole('button', { name: 'that are ready' }).click()
  await assertAccessible(page)
  await page.getByRole('button', { name: 'accepted changes' }).click()
  await expect(page.getByText('Applied as version 2 of found-by-aws.')).toBeVisible()
  await page.getByRole('link', { name: 'Open it in the Studio to finish by hand' }).click()
  await expect(page.locator('.react-flow__node-estate').filter({ hasText: 'fintech-receipts' }).first()).toBeVisible()
  gate.assertClean()
})
