import { chromium } from '@playwright/test'
import { BASE, CTO, totp } from './ledgerly.mjs'
const out = process.argv[2]
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 810 } })
await page.goto(BASE)
await page.locator('#email').fill(CTO.email)
await page.locator('#password').fill(CTO.password)
await page.getByRole('button', { name: 'Sign in' }).click()
await page.waitForTimeout(1500)
await page.screenshot({ path: `${out}/00-after-password.png` })
const code = page.getByRole('textbox').first()
await code.fill(totp(CTO.totp))
await page.keyboard.press('Enter')
await page.waitForTimeout(3000)
for (const [name, url] of [['01-home', '/'], ['02-setup', '/p/main/setup'], ['03-overview', '/p/main'], ['04-studio', '/p/main/studio'], ['05-approvals', '/p/main/approvals'], ['06-registry', '/p/main/registry'], ['07-settings', '/settings'], ['08-agents', '/p/main/agents'], ['09-fixfactory', '/p/main/fix-factory']]) {
  await page.goto(BASE + url); await page.waitForTimeout(2500)
  await page.screenshot({ path: `${out}/${name}.png` })
  console.log(name, (await page.locator('main').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 300))
}
await browser.close()
