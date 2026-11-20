// PG.3: look at the imported twin on the deployed platform as the engineer, Scene and Plan, at 1440 px. Own isolated headless browser.
// WH_PASSWORD from the environment. Read only: opens the clone, changes nothing.
import { createRequire } from 'node:module'
const require = createRequire(process.env.PW_FROM || import.meta.url)
const { chromium } = require('@playwright/test')
const BASE = process.argv[2]
const OUT = process.argv[3]
const browser = await chromium.launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
const problems = []
page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`) })
page.on('requestfailed', (r) => problems.push(`failed: ${r.url()}`))
await page.goto(BASE)
await page.locator('#email').fill('engineer@acme.example')
await page.locator('#password').fill(process.env.WH_PASSWORD)
await page.getByRole('button', { name: 'Sign in' }).click()
await page.getByRole('heading', { level: 1, name: 'Projects' }).waitFor()
await page.getByRole('link', { name: 'Open Fintech shop' }).click()
await page.getByRole('heading', { level: 1, name: 'Overview' }).waitFor()
await page.goto(`${BASE}/studio`)
await page.getByRole('toolbar', { name: 'Clone' }).waitFor()
await page.getByRole('combobox', { name: 'Clone' }).click()
await page.getByRole('option', { name: /proving-ground-imported/ }).click()
await page.getByRole('toolbar', { name: 'Clone' }).getByText('proving-ground-imported').waitFor()
await page.waitForTimeout(2500)
for (const view of ['Scene', 'Plan']) {
  await page.getByRole('button', { name: `${view} view` }).click()
  await page.waitForTimeout(2500)
  try { await page.getByRole('button', { name: 'Fit to screen' }).click() } catch {}
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `${OUT}/twin-${view.toLowerCase()}-1440.png` })
}
await page.getByRole('button', { name: 'Zoom in' }).click({ clickCount: 1 }).catch(() => {})
await page.getByRole('button', { name: 'Zoom in' }).click().catch(() => {})
await page.waitForTimeout(800)
await page.screenshot({ path: `${OUT}/twin-plan-zoomed-1440.png` })
await page.getByText('Problems', { exact: true }).click().catch(() => {})
await page.waitForTimeout(800)
await page.screenshot({ path: `${OUT}/twin-plan-problems-1440.png` })
console.log('problems:', JSON.stringify(problems))
await browser.close()
