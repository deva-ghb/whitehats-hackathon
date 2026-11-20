/** A choice that is not made yet stays empty: selecting another item of the same type must not show (or lose) the choice made on the first. */

import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

const library = (page) => page.getByRole('complementary', { name: 'Node library' })
const inspector = (page) => page.getByRole('complementary', { name: 'Inspector' })

async function drop(page, x, y) {
  await library(page).getByRole('button', { name: 'Serverless function' }).dragTo(page.locator('.studio-canvas'), { targetPosition: { x, y } })
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
}

test('a choice made on one function is not shown on the next one, and choosing it there is saved', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/studio`)
  await page.getByRole('button', { name: 'Start a blank clone' }).click()
  await page.getByLabel('Clone name').fill('two-functions')
  await expect(async () => {
    await page.getByRole('button', { name: 'Create clone' }).click()
    await expect(page.getByRole('heading', { name: 'This clone is empty' })).toBeVisible({ timeout: 1500 })
  }).toPass()
  // The plan (flat) view: the isometric scene is the other choice and has its own tests.
  await page.getByRole('button', { name: 'Plan view' }).click()
  await expect(page.getByRole('button', { name: 'Plan view' })).toHaveAttribute('aria-pressed', 'true')

  await drop(page, 200, 200)
  await inspector(page).getByLabel('Name', { exact: true }).fill('first')
  const platform = inspector(page).getByRole('combobox', { name: 'Platform', exact: true })
  await platform.click()
  await page.getByRole('option', { name: 'aws_lambda', exact: true }).click()
  await expect(platform).toContainText('aws_lambda')

  await drop(page, 200, 400)
  await inspector(page).getByLabel('Name', { exact: true }).fill('second')
  await expect(inspector(page)).toContainText('Stays Draft until Platform, Runtime are filled.')
  await expect(platform, 'nothing has been chosen on this function yet').not.toContainText('aws_lambda')

  await platform.click()
  await page.getByRole('option', { name: 'aws_lambda', exact: true }).click()
  await expect(inspector(page)).toContainText('Stays Draft until Runtime is filled.')
  await expect(page.getByTestId('save-state')).toHaveText('Saved')
  gate.assertClean()
})
