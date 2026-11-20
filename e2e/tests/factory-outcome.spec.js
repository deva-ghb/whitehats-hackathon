/** A fix proven and merged in Fix Factory unblocks Resolve on its incident (R-010), from a recorded run. */

import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { assertAccessible, attachGate, shoot, tabTo } from '../gate.js'
import { startRecordedFactory } from '../recorded-factory.js'
import { signIn } from '../sign-in.js'

const test = base.extend({
  app: [async ({}, use) => {
    const engine = await startRecordedFactory()
    const started = await startApp(engine.env)
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
    await engine.stop()
  }, { scope: 'worker' }],
})

const SQL_FIX = 'Fix SQL injection in user lookup'
const ROUTE_FIX = 'Disable the user lookup route'

async function openIncident(page, baseURL) {
  await page.goto(`${baseURL}/incidents`)
  await page.getByRole('row', { name: 'INC-2208' }).click()
  await expect(page.getByRole('heading', { name: 'Proposed fixes' })).toBeVisible()
}

function fixRow(page, title) {
  return page.locator('.list-item', { hasText: title })
}

test('a fix merged in Fix Factory lets the incident be resolved', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)

  await page.goto(`${app.baseURL}/approvals`)
  const sqlCard = page.locator('.card', { has: page.getByRole('heading', { name: SQL_FIX }) })
  await sqlCard.getByRole('button', { name: 'Apply Fix' }).click()
  await expect(page.getByText('Sent to Fix Factory to be proven')).toBeVisible()
  const routeCard = page.locator('.card', { has: page.getByRole('heading', { name: ROUTE_FIX }) })
  await routeCard.getByRole('button', { name: 'Approve', exact: true }).click()
  await page.getByRole('row', { name: ROUTE_FIX }).getByRole('button', { name: 'Mark as done' }).click()
  await expect(page.getByRole('row', { name: ROUTE_FIX })).toContainText('Marked done by Anita Rao')

  await openIncident(page, app.baseURL)
  await expect(fixRow(page, SQL_FIX)).toContainText('approved')
  await page.locator('#change-status').click()
  await page.locator('#incident-status').click()
  await expect(page.getByRole('option', { name: /Fixed, please verify/ })).toHaveAttribute('aria-disabled', 'true')
  await expect(page.getByText(`Not verified yet: ${SQL_FIX}.`)).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Cancel' }).click()

  await page.goto(`${app.baseURL}/fix-factory`)
  await page.getByText('SQL injection in user lookup', { exact: true }).click()
  await expect(page.getByText('Awaiting approval').first()).toBeVisible()
  await page.getByRole('button', { name: 'Approve and merge' }).click()
  await expect(page.getByText('Merged').first()).toBeVisible()

  await openIncident(page, app.baseURL)
  await expect(fixRow(page, SQL_FIX)).toContainText('verified')
  await expect(fixRow(page, SQL_FIX)).toContainText('Approved and merged into demo at 38696a9e58.')
  await expect(page.getByText(`Fix Factory proved and delivered: ${SQL_FIX}`)).toBeVisible()
  await assertAccessible(page)

  // Say it is fixed by keyboard alone: allowed now the fix is proven. WhiteHats checks again, then resolves it (FD.2).
  await tabTo(page, 'Change status')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.locator('#incident-status')).toContainText('Fixed, please verify')
  await page.locator('#status-note').focus()
  await page.keyboard.type('The proven fix is merged and the route is back')
  await tabTo(page, 'Update status')
  await page.keyboard.press('Enter')
  await expect(page.locator('.page-head')).toContainText('WhiteHats is checking')
  await expect(page.locator('.page-head')).toContainText('resolved', { timeout: 45_000 })
  await page.getByRole('button', { name: 'Undo' }).waitFor({ state: 'detached', timeout: 15_000 })

  const signalTimes = page.locator('.card', { hasText: 'What each WhiteHat saw' }).locator('.list-item .muted')
  const times = [page.locator('.num'), signalTimes]
  await shoot(page, 'factory-outcome-resolved', { mask: times })
  gate.assertClean()
})
