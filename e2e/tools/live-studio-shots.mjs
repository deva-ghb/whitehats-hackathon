// Screenshots of the live Studio on the deployed server, signed in through the sign-in screen.
// The password comes from WH_LIVE_PASSWORD (read from Parameter Store by the caller) and is never printed.
import { chromium } from '@playwright/test'

const base = process.env.WH_LIVE_URL || 'https://d1sminperzg6yf.cloudfront.net'
const out = process.argv[2]
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await page.goto(base)
await page.locator('#email').fill(process.env.WH_LIVE_EMAIL || 'ceo@acme.example')
await page.locator('#password').fill(process.env.WH_LIVE_PASSWORD)
await page.getByRole('button', { name: 'Sign in' }).click()
await page.getByRole('heading', { level: 1, name: 'Projects' }).waitFor({ timeout: 30_000 })
await page.goto(`${base}/p/main/studio`)
await page.getByRole('application', { name: /Isometric scene/ }).waitFor({ timeout: 30_000 })
await page.waitForTimeout(2500)
for (const width of [1440, 1920]) {
  await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 })
  await page.waitForTimeout(1200)
  await page.screenshot({ path: `${out}/studio-${width}.png` })
}
const waiting = page.locator('.studio-waiting')
console.log('waiting link:', await waiting.count() ? await waiting.innerText() : 'none')
if (await waiting.count()) {
  await waiting.click()
  await page.waitForTimeout(2500)
  await page.screenshot({ path: `${out}/waiting-1920.png` })
}
await browser.close()
