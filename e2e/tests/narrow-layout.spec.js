/** At a phone width, signal titles stay readable and status hints fill the field. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-034')

async function signalTitlesStayReadable(page) {
  const signalCard = page.locator('section.card', { has: page.getByRole('heading', { name: 'Latest signals' }) })
  const titles = signalCard.locator('.signal-title')
  await expect(titles.first()).toBeVisible()
  const count = await titles.count()
  expect(count).toBeGreaterThan(0)
  for (let i = 0; i < count; i++) {
    const title = titles.nth(i)
    if (!(await title.isVisible())) continue
    await title.scrollIntoViewIfNeeded()
    const { full, shown } = await title.evaluate((el) => {
      const full = el.textContent || ''
      const style = getComputedStyle(el)
      const clips = (style.overflowX === 'hidden' || style.overflow === 'hidden')
        && (style.whiteSpace === 'nowrap' || style.whiteSpace === 'pre')
        && el.scrollWidth > el.clientWidth + 1
      if (!clips) return { full, shown: full.length }
      const node = [...el.childNodes].find((child) => child.nodeType === Node.TEXT_NODE)
      if (!node || !node.textContent) return { full, shown: full.length }
      const range = document.createRange()
      const right = el.getBoundingClientRect().right
      let lo = 0
      let hi = node.textContent.length
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2)
        range.setStart(node, 0)
        range.setEnd(node, mid)
        if (range.getBoundingClientRect().right <= right + 0.5) lo = mid
        else hi = mid - 1
      }
      return { full, shown: lo }
    })
    expect(shown, `"${full}" shows ${shown} characters`).toBeGreaterThanOrEqual(Math.min(20, full.length))
  }
}

async function hintsFillTheField(page) {
  const dialog = page.getByRole('dialog', { name: 'Change status' })
  const fields = dialog.locator('.field:has(.help)')
  await expect(fields.first()).toBeVisible()
  const fieldCount = await fields.count()
  expect(fieldCount).toBeGreaterThan(0)
  for (let i = 0; i < fieldCount; i++) {
    const widths = await fields.nth(i).evaluate((el) => {
      const hint = el.querySelector('.help')
      const style = hint ? getComputedStyle(hint) : null
      return {
        field: el.clientWidth,
        hint: hint ? hint.clientWidth : 0,
        display: style ? style.display : '',
      }
    })
    expect(widths.display, 'the hint is a full-width block, not the help-page grid').toBe('block')
    expect(widths.hint, `hint is ${widths.hint}px in a ${widths.field}px field`).toBeGreaterThanOrEqual(widths.field * 0.9)
  }
}

test('signal titles and status hints keep their width at 390px', async ({ page, app }) => {
  await mkdir(SHOTS, { recursive: true })
  await signIn(page, app.baseURL)
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.getByRole('heading', { name: 'Latest signals' })).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'overview-1440.png'), fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })

  await signalTitlesStayReadable(page)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await assertAccessible(page)
  await page.screenshot({ path: path.join(SHOTS, 'overview-390.png'), fullPage: true })

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${app.baseURL}/incidents`)
  await page.getByRole('row', { name: /INC-/ }).first().click()
  await page.locator('#change-status').click()
  await hintsFillTheField(page)
  await page.screenshot({ path: path.join(SHOTS, 'change-status-1440.png') })

  await page.setViewportSize({ width: 390, height: 844 })
  await hintsFillTheField(page)
  const dialogOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(dialogOverflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: path.join(SHOTS, 'change-status-390.png') })
})
