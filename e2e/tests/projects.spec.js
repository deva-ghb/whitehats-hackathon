/** Account home, project switcher and project settings (checklist AP.4). */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, shoot } from '../gate.js'
import { DEMO_PASSWORD, signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/AP.4')
const ROLLUP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/AP.5')

async function signInOnHome(page, baseURL, email = 'ceo@acme.example') {
  await page.goto(baseURL)
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(DEMO_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('heading', { level: 1, name: 'Projects' }).waitFor()
}

async function choose(page, id, name) {
  await page.locator(`#${id}`).click()
  await page.getByRole('option', { name }).click()
}

test('account home, project settings and account settings pass the gate', async ({ page, app }) => {
  test.setTimeout(180_000)
  const gate = attachGate(page)
  await signInOnHome(page, app.baseURL)
  await expect(page.getByRole('heading', { level: 2, name: 'Account score' })).toBeVisible()
  await expect(page.getByText(/Lowest project:/)).toBeVisible()
  await assertAccessible(page)
  await page.mouse.move(0, 0)  // the sign-in click leaves the pointer over a card, which would shoot its hover state
  await shoot(page, 'account-home')
  await mkdir(SHOTS, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'account-home-1440.png'), fullPage: true })

  await page.getByRole('link', { name: 'Open Fintech shop' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
  await page.getByRole('link', { name: 'Project settings' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Project settings' })).toBeVisible()
  await expect(page.locator('#settings-name')).toBeVisible()
  await assertAccessible(page)
  await shoot(page, 'project-settings')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'project-settings-1440.png'), fullPage: true })

  await page.getByRole('link', { name: 'Account settings' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Connections' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Runner' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'AI models' })).toBeVisible()
  await assertAccessible(page)
  await shoot(page, 'account-settings')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'account-settings-1440.png'), fullPage: true })
  gate.assertClean()
})

test('create each kind of project, switch, and a viewer sees why approve is off', async ({ page, app }) => {
  test.setTimeout(120_000)
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  await page.getByRole('link', { name: 'Projects' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Projects' })).toBeVisible()

  await page.getByRole('button', { name: 'New project' }).click()
  await page.locator('#project-name').fill('Quiet desk')
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
  await expect(page).toHaveURL(/\/p\/quiet-desk\/?$/)

  await page.getByRole('link', { name: 'Projects' }).click()
  await page.getByRole('button', { name: 'New project' }).click()
  await page.locator('#project-name').fill('Health desk')
  await choose(page, 'start-from', 'Health template')
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page).toHaveURL(/\/p\/health-desk/)

  await page.getByRole('link', { name: 'Projects' }).click()
  await page.getByRole('button', { name: 'New project' }).click()
  await page.locator('#project-name').fill('Scoped desk')
  await choose(page, 'start-from', 'A connection')
  await choose(page, 'project-connection', 'AWS CloudWatch Logs')
  await page.locator('#project-region').fill('ap-southeast-2')
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page).toHaveURL(/\/p\/scoped-desk/)

  await page.getByRole('link', { name: 'Projects' }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'Account score' })).toBeVisible()
  const scores = page.getByRole('list', { name: 'Project scores' })
  await expect(scores).toContainText('Quiet desk')
  await expect(scores).toContainText('Health desk')
  await expect(scores).toContainText('Scoped desk')
  await expect(page.getByText(/Lowest project: .+, score \d+\./)).toBeVisible()
  await page.setViewportSize({ width: 1440, height: 900 })
  await mkdir(ROLLUP, { recursive: true })
  await page.screenshot({ path: path.join(ROLLUP, 'account-overview-1440.png'), fullPage: true })
  await page.getByRole('link', { name: 'Open Quiet desk' }).click()
  await expect(page).toHaveURL(/\/p\/quiet-desk/)
  await page.getByRole('link', { name: 'Project settings' }).click()
  await expect(page).toHaveURL(/\/p\/quiet-desk\/settings/)
  await page.locator('#member-email').fill('analyst@acme.example')
  await choose(page, 'member-role', 'Viewer')
  await page.locator('#member-reason').fill('Reads the quiet desk')
  await page.getByRole('button', { name: 'Invite to this project' }).click()
  await expect(page.getByText('analyst@acme.example')).toBeVisible()

  await page.getByRole('button', { name: 'Sign out' }).click()
  await signIn(page, app.baseURL, 'analyst@acme.example')
  await page.getByLabel('Project').selectOption({ label: 'Quiet desk' })
  await expect(page).toHaveURL(/\/p\/quiet-desk\/?$/)
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Approvals' }).click()
  await expect(page).toHaveURL(/\/p\/quiet-desk\/approvals/)
  await expect(page.getByText('Only Approvers in this project can approve.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve' })).toBeDisabled()
  gate.assertClean()
})
