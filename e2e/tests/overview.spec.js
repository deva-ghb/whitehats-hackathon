/** The first browser journey: a CEO opens Overview, at the two widths every screen is checked at. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/P0.6')
const WIDTHS = [
  { width: 1440, height: 900, name: '1440' },
  { width: 390, height: 844, name: '390' },
]

test('ceo signs in and opens Overview', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  await expect(page.getByText('Threat posture')).toBeVisible()
  await mkdir(SHOTS, { recursive: true })
  for (const size of WIDTHS) {
    await page.setViewportSize({ width: size.width, height: size.height })
    await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
    await page.screenshot({ path: path.join(SHOTS, `overview-${size.name}.png`), fullPage: true })
    if (size.width === 390) {
      const overflow = await page.evaluate(() => (
        document.documentElement.scrollWidth - document.documentElement.clientWidth
      ))
      expect(overflow).toBeLessThanOrEqual(1)
    }
  }
  gate.assertClean()
})
