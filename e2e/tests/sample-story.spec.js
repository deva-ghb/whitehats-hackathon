/** Each open sample incident tells one story on its own page. */

import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/R-033')

const OPEN = [
  {
    ref: 'INC-2208',
    title: 'SQL injection in user lookup',
    signal: 'Log shape needs review: error SELECT * FROM users for lookup',
    fix: 'Fix SQL injection in user lookup',
    absent: 'Remote Desktop on port 3389',
  },
  {
    ref: 'INC-2211',
    title: 'Root account used for AttachUserPolicy and a new access key',
    signal: 'Root account used for AttachUserPolicy',
    also: 'A new access key was created',
    absent: 'Live payments',
  },
  {
    ref: 'INC-2214',
    title: 'SSH and Remote Desktop accept connections from any network',
    signal: 'SSH on port 22 accepts connections from any network',
    also: 'Remote Desktop on port 3389 accepts connections from any network',
    absent: 'payments key',
  },
]

test('the three open sample incidents each tell one story', async ({ page, app }) => {
  await mkdir(SHOTS, { recursive: true })
  await signIn(page, app.baseURL)
  await expect(page.getByText('Unseen', { exact: true })).toBeVisible()
  await expect(page.getByText('396', { exact: true })).toBeVisible()  // FD.2: no incident is "contained" (half weight) any more
  await expect(page.getByText('Fix SQL injection in user lookup').first()).toBeVisible()
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: path.join(SHOTS, 'overview-1440.png'), fullPage: true })

  for (const story of OPEN) {
    await page.goto(`${app.baseURL}/incidents`)
    await page.getByRole('row', { name: new RegExp(story.ref) }).click()
    await expect(page.getByRole('heading', { level: 2, name: story.title })).toBeVisible()
    await expect(page.getByText(story.signal, { exact: true })).toBeVisible()
    if (story.also) await expect(page.getByText(story.also, { exact: true })).toBeVisible()
    if (story.fix) await expect(page.getByText(story.fix, { exact: true })).toBeVisible()
    await expect(page.getByText(story.absent)).toHaveCount(0)
    const shot = story.ref.toLowerCase()
    await page.screenshot({ path: path.join(SHOTS, `${shot}-1440.png`), fullPage: true })
  }
})
