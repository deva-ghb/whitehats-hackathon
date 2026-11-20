/** Suggested questions come from this company's records, and the offline answer says so. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-023')

test('suggestions follow open incidents and the answer says no model is connected', async ({ page, app }) => {
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/assistant`)
  await expect(page.getByRole('heading', { name: 'Ask anything about your security' })).toBeVisible()
  const suggestions = page.locator('.suggest button')
  await expect(suggestions.first()).toBeVisible()
  const texts = await suggestions.allTextContents()
  const openRefs = await page.evaluate(async () => {
    const res = await fetch('/api/incidents?status=open', {
      headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}` },
    })
    return (await res.json()).map((row) => row.ref)
  })
  expect(openRefs.length).toBeGreaterThan(0)
  expect(texts.some((text) => openRefs.some((ref) => text.includes(ref)))).toBe(true)
  expect(texts.join(' ')).not.toContain('INC-9999')
  await assertAccessible(page)

  await mkdir(SHOTS, { recursive: true })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'assistant-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, 'assistant-390.png'), fullPage: true })

  await page.setViewportSize({ width: 1440, height: 900 })
  await suggestions.first().click()
  await expect(page.locator('.msg.assistant').last()).toContainText('No model is connected')
  await page.evaluate(async () => {
    await fetch('/api/assistant/history', {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}` },
    })
  })
})

const LAYOUT_SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-039')

async function chatLayout(page) {
  return page.evaluate(() => {
    const log = document.querySelector('.chat-log')
    return {
      pageOverflow: document.documentElement.scrollHeight - document.documentElement.clientHeight,
      pageScrolled: window.scrollY,
      logScrolls: log.scrollHeight > log.clientHeight,
      logGapBelow: log.scrollHeight - log.clientHeight - log.scrollTop,
    }
  })
}

test('the chat scrolls its own messages and never the page', async ({ page, app }) => {
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/assistant`)
  await expect(page.getByRole('heading', { name: 'Ask anything about your security' })).toBeVisible()
  await page.locator('#question').focus()
  for (let n = 1; n <= 4; n += 1) {
    await page.keyboard.type(`What changed in the last day? (${n})`)
    await page.keyboard.press('Enter')
    // Only a finished answer has .meta; the "Reading your data" placeholder is also a .msg.assistant.
    await expect(page.locator('.msg.assistant .meta')).toHaveCount(n)
  }
  await mkdir(LAYOUT_SHOTS, { recursive: true })
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.keyboard.type(`Anything new at ${width} pixels?`)
    await page.keyboard.press('Enter')
    await expect(page.locator('.msg.assistant .meta')).toHaveCount(width === 1440 ? 5 : 6)
    await expect.poll(() => chatLayout(page)).toEqual({ pageOverflow: 0, pageScrolled: 0, logScrolls: true, logGapBelow: 0 })
    await expect(page.locator('.msg.assistant').last()).toBeInViewport()
    await page.screenshot({ path: path.join(LAYOUT_SHOTS, `assistant-chat-${width}.png`), fullPage: true })
  }
  await page.getByRole('button', { name: 'Clear' }).click()
  await expect(page.locator('.msg')).toHaveCount(0)
})
