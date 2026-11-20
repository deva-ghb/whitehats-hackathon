/** Shared checks for every screen: console, failed requests, keyboard, and the five widths. */

import AxeBuilder from '@axe-core/playwright'
import { expect } from '@playwright/test'

export const WIDTHS = [390, 768, 1024, 1440, 1920]

export function attachGate(page) {
  const problems = []
  page.on('console', (message) => {
    if (message.type() === 'error') problems.push(`console: ${message.text()}`)
  })
  page.on('requestfailed', (request) => {
    const error = request.failure()?.errorText || 'failed'
    if (error === 'net::ERR_ABORTED') return
    problems.push(`network: ${request.method()} ${request.url()} ${error}`)
  })
  page.on('response', (response) => {
    if (response.status() < 400) return
    if (!response.url().includes('/api/')) return
    problems.push(`http ${response.status()} ${response.url()}`)
  })
  return {
    problems,
    assertClean() {
      expect(problems, problems.join('\n')).toEqual([])
    },
  }
}

export async function assertAccessible(page, include) {
  const builder = include ? new AxeBuilder({ page }).include(include) : new AxeBuilder({ page })
  const results = await builder.analyze()
  const blocking = results.violations.filter((item) => item.impact === 'serious' || item.impact === 'critical')
  const summary = blocking.flatMap((item) => item.nodes.slice(0, 12).map((node) => (
    `${item.id}: ${node.target.join(' ')} :: ${node.html.replace(/\s+/g, ' ').slice(0, 140)}`
  )))
  expect(summary, summary.join('\n')).toEqual([])
}

// What the run's own clock decides: dates written as <time>, and charts drawn against today.
// Masked so a baseline taken one day still matches the next.
const DATED = 'time, svg.line-chart'

/** options go to toHaveScreenshot; a mask given there is added to the dated mask. */
export async function shoot(page, name, options = {}) {
  const mask = [page.locator(DATED), ...(options.mask || [])]
  await page.evaluate(() => document.fonts.ready)  // a fallback font wraps text differently
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await expect(page).toHaveScreenshot(`${name}-${width}.png`, { fullPage: true, ...options, mask })
    if (width === 390) {
      const overflow = await page.evaluate(() => (
        document.documentElement.scrollWidth - document.documentElement.clientWidth
      ))
      expect(overflow).toBeLessThanOrEqual(1)
    }
  }
}

/**
 * Press Tab until focus reaches target. No mouse.
 * target is a Locator for one element, or text the focused control must contain.
 */
export async function tabTo(page, target) {
  const byText = typeof target === 'string'
  if (!byText) await expect(target).toBeVisible()
  for (let step = 0; step < 80; step += 1) {
    if (byText ? await focusedTextIncludes(page, target) : await target.evaluate((el) => el === document.activeElement)) {
      return
    }
    await page.keyboard.press('Tab')
  }
  throw new Error(`Tab never reached ${byText ? `"${target}"` : target}`)
}

async function focusedTextIncludes(page, name) {
  const focused = page.locator(':focus')
  if (!(await focused.count())) return false
  const text = await focused.innerText().catch(() => '')
  return text.includes(name)
}
