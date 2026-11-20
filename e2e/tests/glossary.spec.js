/** GL.1 and GL.2: the platform's own words are explained where they appear, by mouse, keyboard and touch; "learning" is a
 * real state that the WhiteHat card shows. */

import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, tabTo } from '../gate.js'
import { signIn } from '../sign-in.js'

const LEARNING = "Switch to Learning: it keeps reading and records what it would have raised, but alerts no one and doesn't change the security score."

function identity(page) {
  return page.locator('.card', { has: page.getByText('Flags a burst of failed sign-ins in collected log lines.') })
}

test('a word is explained on hover and on keyboard focus, read as its description, and closed with Escape', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/agents`)
  const card = identity(page)
  const button = card.getByRole('button', { name: 'Mark learning' })
  const bubble = page.getByRole('tooltip', { name: LEARNING })

  await expect(button).toHaveAccessibleDescription(LEARNING)  // a screen reader hears it without opening anything
  await button.hover()
  await expect(bubble).toBeVisible()
  await page.mouse.move(0, 0)
  await expect(bubble).toBeHidden()

  await tabTo(page, button)
  await expect(bubble).toBeVisible()
  const box = await bubble.boundingBox()
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width)
  await page.keyboard.press('Escape')
  await expect(bubble).toBeHidden()

  await card.getByText('Coverage').hover()
  await expect(page.getByRole('tooltip', { name: /^Coverage: how much of what this WhiteHat should see/ })).toBeVisible()
  await assertAccessible(page)
  gate.assertClean()
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  test('a tap explains a label, and a press-and-hold explains a button without pressing it', async ({ page, app }) => {
    const gate = attachGate(page)
    await signIn(page, app.baseURL, 'engineer@acme.example')
    await page.goto(`${app.baseURL}/agents`)
    const card = identity(page)
    await card.locator('.pill', { hasText: 'Waiting for data' }).tap()
    const waiting = page.getByRole('tooltip', { name: /^Waiting for data: switched on/ })
    await expect(waiting).toBeVisible()
    const box = await waiting.boundingBox()
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(390)

    const button = card.getByRole('button', { name: 'Mark dark' })
    await button.scrollIntoViewIfNeeded()
    const at = await button.boundingBox()
    const point = { pointerType: 'touch', isPrimary: true, clientX: at.x + 10, clientY: at.y + 10 }
    await button.dispatchEvent('pointerdown', point)
    await page.waitForTimeout(600)
    await button.dispatchEvent('pointerup', point)
    await button.dispatchEvent('click')
    await expect(page.getByRole('tooltip', { name: /^Switch to Dark/ })).toBeVisible()
    await expect(card.locator('.pill', { hasText: 'Waiting for data' })).toBeVisible()  // the hold did not switch it off
    gate.assertClean()
  })
})

test('marking a WhiteHat learning shows it as learning, explains that, and counts what it would have raised', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/agents`)
  const card = identity(page)
  await card.getByRole('button', { name: 'Mark learning' }).click()
  await expect(card).toContainText('0 it would have raised while learning')
  await card.getByText('0 it would have raised while learning').hover()
  await expect(page.getByRole('tooltip', { name: /alerted no one/ })).toBeVisible()
  await card.getByRole('button', { name: 'Mark active' }).click()
  await expect(card).not.toContainText('would have raised')
  gate.assertClean()
})
