/** Sample rows wear a Sample tag. The banner is only on the seeded demo company. */

import { execFileSync } from 'node:child_process'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { DEMO_PASSWORD, signIn } from '../sign-in.js'
import { totp } from '../totp.js'

const BANNER = 'This workspace shows sample data. Connect a source to see your own.'

async function eachHasSample(locator) {
  await expect(locator.first()).toBeVisible()
  const count = await locator.count()
  expect(count).toBeGreaterThan(0)
  for (let i = 0; i < count; i++) {
    await expect(locator.nth(i).getByText('Sample', { exact: true })).toBeVisible()
  }
}

async function operatorToken(request, baseURL) {
  const login = await request.post(`${baseURL}/api/auth/login`, {
    data: { email: 'operator@whitehats.example', password: DEMO_PASSWORD },
  })
  const step = await login.json()
  expect(step.step).toBe('mfa_setup')
  const setup = await request.post(`${baseURL}/api/auth/mfa/setup`, { data: { step_token: step.step_token } })
  const { secret } = await setup.json()
  const done = await request.post(`${baseURL}/api/auth/mfa/confirm`, {
    data: { step_token: step.step_token, code: totp(secret) },
  })
  const body = await done.json()
  expect(body.step).toBe('done')
  expect(body.org.sample).toBe(false)
  return body.token
}

function databasePath(baseURL) {
  const port = new URL(baseURL).port
  const pids = execFileSync('lsof', ['-nP', '-t', `-iTCP:${port}`, '-sTCP:LISTEN'], { encoding: 'utf8' })
    .trim().split('\n').filter(Boolean)
  for (const pid of pids) {
    const files = execFileSync('lsof', ['-nP', '-p', pid], { encoding: 'utf8' })
    const hit = files.split('\n').map((line) => line.trim().split(/\s+/).at(-1))
      .find((name) => name && name.endsWith('whitehats.db'))
    if (hit) return hit
  }
  throw new Error('Could not find the test database')
}

function restoreOperator(baseURL) {
  const path = databasePath(baseURL)
  execFileSync('python3', ['-c', `
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=30)
conn.execute("UPDATE users SET totp_secret_enc = '', mfa_enrolled_at = NULL WHERE email = 'operator@whitehats.example'")
conn.commit()
conn.execute("PRAGMA foreign_keys=ON")
try:
    conn.execute("DELETE FROM organizations WHERE slug = 'plainco'")
    conn.commit()
except sqlite3.IntegrityError:
    conn.rollback()
`, path], { encoding: 'utf8' })
}

test('every sample item is tagged and the banner is only on the demo company', async ({ page, app, request }) => {
  test.setTimeout(120_000)
  let op = null
  let companyId = null
  let operatorTouched = false
  try {
    await signIn(page, app.baseURL)
    await expect(page.getByRole('status').getByText(BANNER)).toBeVisible()
    await expect(page.getByText('Probe timing matches known AI attack tooling')).toHaveCount(0)
    await expect(page.getByText('attack tooling')).toHaveCount(0)

    const openCard = page.locator('section.card', { has: page.getByRole('heading', { name: 'Open incidents' }) })
    await eachHasSample(openCard.locator('a.list-item'))
    const signalCard = page.locator('section.card', { has: page.getByRole('heading', { name: 'Latest signals' }) })
    await eachHasSample(signalCard.locator('.list-item'))
    const decisionCard = page.locator('section.card', { has: page.getByRole('heading', { name: 'Decisions waiting for you' }) })
    await eachHasSample(decisionCard.locator('.decision', { hasText: 'Fix to approve' }))

    await page.setViewportSize({ width: 390, height: 844 })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
    await page.setViewportSize({ width: 1440, height: 900 })

    await page.goto(`${app.baseURL}/incidents`)
    await expect(page.getByRole('status').getByText(BANNER)).toBeVisible()
    await eachHasSample(page.locator('table.t tbody tr'))
    await page.getByRole('tab', { name: 'All' }).click()
    await expect(page.locator('table.t tbody tr')).toHaveCount(4)
    const hrefs = await page.locator('table.t tbody tr a.row-link').evaluateAll((els) => els.map((el) => el.getAttribute('href')))
    await eachHasSample(page.locator('table.t tbody tr'))
    for (const href of hrefs) {
      await page.goto(`${app.baseURL}${href}`)
      await expect(page.locator('.page-head').getByText('Sample', { exact: true })).toBeVisible()
      const signals = page.locator('section.card', { has: page.getByRole('heading', { name: 'Signals' }) }).locator('.list-item')
      await eachHasSample(signals)
      const fixes = page.locator('section.card', { has: page.getByRole('heading', { name: 'Proposed fixes' }) }).locator('.list-item')
      if (await fixes.count()) await eachHasSample(fixes)
      await expect(page.getByText('attack tooling')).toHaveCount(0)
    }

    await page.goto(`${app.baseURL}/approvals`)
    await eachHasSample(page.locator('.grid.g2 section.card h3'))
    await eachHasSample(page.locator('section.card', { has: page.getByRole('heading', { name: 'Decided' }) }).locator('tbody tr'))

    await page.goto(`${app.baseURL}/setup`)
    await expect(page.getByRole('status').getByText(BANNER)).toBeVisible()
    await eachHasSample(page.locator('.check', { hasText: 'Recorded during the demo setup' }))
    await expect(page.locator('.check', { hasText: 'Demo domain acme.example' }).getByText('Sample', { exact: true })).toBeVisible()
    await expect(page.locator('.check', { hasText: 'Data stored in the agreed region' }).getByText('Sample', { exact: true })).toHaveCount(0)
    const milestones = page.locator('section.card', { has: page.getByRole('heading', { name: 'Time to first value' }) })
    await expect(milestones.getByText('Sample', { exact: true })).toBeVisible()
    const agreement = page.locator('section.card', { has: page.getByRole('heading', { name: 'Your agreement' }) })
    await expect(agreement.getByText('Sample', { exact: true })).toBeVisible()

    operatorTouched = true
    op = await operatorToken(request, app.baseURL)
    const created = await request.post(`${app.baseURL}/api/operator/tenants`, {
      headers: { Authorization: `Bearer ${op}` },
      data: {
        name: 'Plain Co', slug: 'plainco', plan: 'starter', region: 'in-south', domain: 'plainco.test',
        mfa_required: false,
        ai_policy: { providers: ['rules'], zero_retention: true, agreed: true },
        contract: { owner_name: 'Pat Lee', owner_email: 'pat@plainco.test', security_contact: 'Pat Lee +91 90000 22222' },
        contracted_agents: ['identity', 'network'],
      },
    })
    const createdBody = await created.json()
    expect(created.ok(), JSON.stringify(createdBody)).toBeTruthy()
    companyId = createdBody.id
    const invited = await request.post(`${app.baseURL}/api/operator/tenants/${companyId}/invites`, {
      headers: { Authorization: `Bearer ${op}` },
      data: { email: 'pat@plainco.test', name: 'Pat Lee', role: 'ceo' },
    })
    const inviteBody = await invited.json()
    expect(invited.ok(), JSON.stringify(inviteBody)).toBeTruthy()
    const inviteToken = inviteBody.link.split('/').at(-1)
    const accepted = await request.post(`${app.baseURL}/api/invites/${inviteToken}/accept`, {
      data: { name: 'Pat Lee', password: 'Plainco-Test-2026' },
    })
    const session = await accepted.json()
    expect(accepted.ok(), JSON.stringify(session)).toBeTruthy()
    expect(session.org.sample).toBe(false)
    await request.post(`${app.baseURL}/api/auth/terms`, {
      headers: { Authorization: `Bearer ${session.token}` },
    })
    await page.evaluate((token) => localStorage.setItem('wh.token', token), session.token)
    await page.goto(app.baseURL)
    await expect(page.getByRole('heading', { level: 1, name: 'Projects' })).toBeVisible()
    await expect(page.getByText('Plain Co')).toBeVisible()
    await expect(page.getByText(BANNER)).toHaveCount(0)
    await page.getByRole('link', { name: 'Open Main' }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
    await expect(page.getByText('Sample', { exact: true })).toHaveCount(0)
    await page.goto(`${app.baseURL}/incidents`)
    await expect(page.getByText('No incidents')).toBeVisible()
    await expect(page.getByText(BANNER)).toHaveCount(0)
    await page.goto(`${app.baseURL}/setup`)
    await expect(page.locator('.check').first()).toBeVisible()
    await expect(page.getByText('Sample', { exact: true })).toHaveCount(0)
    await page.goto(`${app.baseURL}/approvals`)
    await expect(page.getByRole('heading', { level: 2, name: 'All caught up' })).toBeVisible()
    await expect(page.getByText(BANNER)).toHaveCount(0)
    await expect(page.getByText('Sample', { exact: true })).toHaveCount(0)
  } finally {
    if (op && companyId) {
      const headers = { Authorization: `Bearer ${op}` }
      await request.post(`${app.baseURL}/api/operator/tenants/${companyId}/offboard`, {
        headers, data: { confirm_name: 'Plain Co', retention_days: 0 },
      })
      await request.post(`${app.baseURL}/api/operator/tenants/${companyId}/delete`, {
        headers, data: { confirm_name: 'Plain Co', early: true },
      })
    }
    if (operatorTouched) restoreOperator(app.baseURL)
  }
})
