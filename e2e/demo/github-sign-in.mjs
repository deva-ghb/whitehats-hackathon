// Opens the recording Chrome profile on GitHub's sign-in page and waits until a person has signed in.
// The profile (outside the repository) keeps the session for every recording take; no password passes through here.
import { chromium } from '@playwright/test'
import os from 'node:os'
import path from 'node:path'

const PROFILE = process.env.DEMO_PROFILE || path.join(os.homedir(), '.whitehats-demo-chrome')
const ctx = await chromium.launchPersistentContext(PROFILE, { headless: false, viewport: { width: 1280, height: 860 } })
const page = ctx.pages()[0] || await ctx.newPage()
await page.goto('https://github.com/login')
console.log('waiting for the GitHub sign-in in the window that opened')
for (;;) {
  await page.waitForTimeout(5000)
  const login = await page.evaluate(() => document.querySelector('meta[name="user-login"]')?.content || '').catch(() => '')
  if (login) { console.log(`signed in as ${login}`); break }
}
await ctx.close()
