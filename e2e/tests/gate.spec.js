/** UI gate for the screens that already exist: Overview, Approvals, Fix Factory. */

import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, shoot, tabTo } from '../gate.js'
import { signIn } from '../sign-in.js'

test('overview, approvals and fix factory pass the gate', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  await expect(page.getByText('Threat posture')).toBeVisible()
  await assertAccessible(page)
  await shoot(page, 'overview')

  await tabTo(page, 'Approvals')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: /decisions wait for you/ })).toBeVisible()
  await assertAccessible(page)
  await shoot(page, 'approvals')

  await tabTo(page, 'Fix Factory')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Fix Factory' })).toBeVisible()
  await expect(page.getByText('Fix Factory engine is not connected')).toBeVisible()
  await assertAccessible(page)
  await shoot(page, 'fix-factory')

  gate.assertClean()
})
