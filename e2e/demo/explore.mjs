// Exploration for the demo recorder: drives the live server as Ledgerly's CTO and screenshots each step.
// node demo/explore.mjs <out-dir> <step...>
import { chromium } from '@playwright/test'
import { BASE, CTO, totp } from './ledgerly.mjs'
const [out, ...steps] = process.argv.slice(2)
import os from 'node:os'
import path from 'node:path'
const browser = await chromium.launchPersistentContext(path.join(os.homedir(), '.whitehats-demo-chrome'),
  { headless: false, viewport: { width: 1440, height: 810 } })
const page = browser.pages()[0] || await browser.newPage()
let n = 0
const shot = async (name) => { await page.waitForTimeout(1500); await page.screenshot({ path: `${out}/${String(n++).padStart(2, '0')}-${name}.png` }); console.log('shot', name) }
await page.goto(BASE, { waitUntil: 'domcontentloaded' })
await Promise.race([page.locator('#email').waitFor(), page.getByText('Alex Morgan').first().waitFor()])
if (await page.locator('#email').isVisible()) {
  await page.locator('#email').fill(CTO.email)
  await page.locator('#password').fill(CTO.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByLabel('Code').waitFor()
  await page.getByLabel('Code').fill(totp(CTO.totp))
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByText('Alex Morgan').first().waitFor({ timeout: 30000 })
}
const SLUG = process.env.SLUG || 'payments-shop'
const act = {
  async newproject() {
    await page.getByRole('button', { name: 'New project' }).click(); await shot('new-project')
    await page.locator('#project-name').fill('Payments shop'); await shot('new-project-filled')
    await page.getByRole('button', { name: 'Create project' }).click(); await shot('created')
  },
  async inspector() {
    await page.goto(`${BASE}/p/${SLUG}/studio`)
    await page.waitForTimeout(5000)
    await page.getByText('AWS connection').first().click().catch(() => {})
    await page.waitForTimeout(5000); await shot('inspector')
    console.log((await page.locator('aside, .inspector').last().innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 1500))
  },
  async awsconnect() {
    await page.goto(`${BASE}/p/${SLUG}/studio`)
    await page.locator('.connection-panel[data-connector=aws]').waitFor({ timeout: 20000 }).catch(async () => {
      await page.getByRole('button', { name: 'Connect a source' }).click(); await page.locator('.connect-source', { hasText: 'AWS' }).first().click() })
    await page.locator('#connection-role_arn').waitFor({ timeout: 20000 })
    const values = { role_arn: 'arn:aws:iam::509594260364:role/whitehats-readonly', external_id: process.env.AWS_EXTERNAL_ID,
      region: 'ap-southeast-2', tag_key: 'project', tag_value: 'whitehats-proving-ground' }
    for (const [k, v] of Object.entries(values)) if (await page.locator(`#connection-${k}`).count()) await page.locator(`#connection-${k}`).fill(v)
    await shot('aws-filled')
    await page.getByRole('button', { name: 'Connect', exact: true }).click()
    await page.waitForTimeout(30000); await shot('aws-connected')
    console.log((await page.locator('.connection-panel').innerText()).replace(/\s+/g, ' ').slice(0, 800))
  },
  async github() {
    await page.goto(`${BASE}/p/${SLUG}/studio`)
    await page.getByRole('button', { name: 'Connect a source' }).click(); await page.locator('.connect-source', { hasText: 'GitHub' }).click()
    await shot('github-placed')
    console.log((await page.locator('.connection-panel, aside').last().innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 600))
    await page.getByRole('button', { name: 'Install the GitHub App' }).click()
    await page.waitForURL(/github\.com/, { timeout: 20000 }); await shot('github-page')
    console.log(page.url())
    const save = page.getByRole('button', { name: 'Save' })
    console.log('save buttons', await save.count(), await save.first().isEnabled().catch(() => 'n/a'))
    await save.first().scrollIntoViewIfNeeded(); await shot('github-save')
    await save.first().click().catch((e) => console.log('save click', e.message.slice(0, 120)))
    await page.waitForTimeout(8000); await shot('github-after-save')
    console.log('after save', page.url())
  },
  async githubinstall() {
    await page.goto(`${BASE}/p/${SLUG}/studio?connection=github`)
    await page.getByRole('button', { name: 'Install the GitHub App' }).click()
    await page.waitForURL(/github\.com/, { timeout: 20000 }); await page.waitForLoadState('domcontentloaded'); await shot('gh-install')
    console.log('at', page.url())
    await page.getByText('Only select repositories').click(); await page.waitForTimeout(800)
    await page.getByRole('button', { name: /Select repositories/ }).click(); await page.waitForTimeout(800)
    await page.keyboard.type('whitehats-proving-ground', { delay: 30 }); await page.waitForTimeout(2500); await shot('gh-search')
    await page.locator('#repository-menu-list button[role=menuitem]').filter({ hasText: 'whitehats-proving-ground' }).first().click()
    await page.waitForTimeout(1000); await page.getByText('Install & Authorize on your personal account').click(); await shot('gh-picked')
    console.log('form', await page.evaluate(() => [...document.querySelectorAll('form input[type=hidden]')].map((i) => `${i.name}=${i.name.includes('token') ? '…' : i.value.slice(0, 60)}`).join(' | ')))
    await page.getByRole('button', { name: 'Install & Authorize' }).click()
    await page.waitForTimeout(4000); console.log('after submit', page.url().slice(0, 150), (await page.locator('.flash, .flash-error, [role=alert]').first().innerText().catch(() => '')).slice(0, 200))
    await page.waitForURL(/cloudfront\.net/, { timeout: 60000 }); await page.waitForTimeout(6000); await shot('gh-back')
    console.log('back at', page.url())
    console.log((await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 900))
  },
  async githubmanual() {
    await page.goto(`${BASE}/p/${SLUG}/studio?connection=github`)
    await page.getByRole('button', { name: 'Install the GitHub App' }).click()
    await page.waitForURL(/github\.com/, { timeout: 20000 })
    console.log('waiting for a person to install on GitHub')
    await page.waitForURL(/cloudfront\.net/, { timeout: 0 }); await page.waitForTimeout(6000); await shot('gh-back-manual')
    console.log('back at', page.url())
    console.log((await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 600))
  },
  async ghinstalls() {
    await page.goto('https://github.com/settings/installations', { waitUntil: 'domcontentloaded' }); await shot('gh-installations')
    console.log((await page.locator('main').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 500))
  },
  async ghappsettings() {
    await page.goto('https://github.com/settings/apps/nativesense-app1', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(2000)
    const read = async () => page.evaluate(() => [...document.querySelectorAll('input, textarea')].filter((i) => i.type !== 'hidden' && !/token|secret/i.test(i.name))
      .map((i) => `${i.name || i.id}=${i.type === 'checkbox' || i.type === 'radio' ? (i.checked ? 'on' : 'off') : i.value.slice(0, 90)}`).join('\n'))
    console.log(await read())
    await page.locator('main').screenshot({ path: `${out}/app-settings.png` }).catch(() => {})
  },
  async ghauthorized() {
    await page.goto('https://github.com/settings/apps/authorizations', { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(1500); await shot('gh-authorized')
    console.log((await page.locator('main').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(-600))
  },
  async merge() {
    await page.goto(process.env.PR_URL, { waitUntil: 'domcontentloaded' }); await page.waitForTimeout(2500); await shot('pr')
    await page.getByRole('button', { name: /^Merge pull request$/ }).click(); await page.waitForTimeout(1200); await shot('confirm')
    await page.getByRole('button', { name: /^Confirm merge$/ }).click()
    await page.getByText(/Pull request successfully merged|Merged/).first().waitFor({ timeout: 60000 }); await shot('merged')
    console.log('merged')
  },
  async studio() { await page.goto(`${BASE}/p/${SLUG}/studio`); await shot('studio') },
  async aws() {
    await page.goto(`${BASE}/p/${SLUG}/studio`)
    await page.getByRole('button', { name: 'Connect a source' }).click(); await shot('sources')
    await page.locator('.connect-source', { hasText: 'AWS' }).first().click(); await shot('aws-panel')
    console.log((await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 1500))
  },
}
try { for (const s of steps) await act[s]() } catch (e) { console.log('FAILED', e.message); await shot('failed') }
await browser.close()
