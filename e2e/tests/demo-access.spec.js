/** AP.7: the demo account as four projects. Each demo person sees exactly what spec 0A gives them. */

import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { WIDTHS, assertAccessible, attachGate, tabTo } from '../gate.js'
import { DEMO_PASSWORD } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/AP.7')
const ALL = ['Fintech shop', 'Health platform', 'SaaS with AI', 'Whole estate']
const SLUG = { 'Fintech shop': 'main', 'Health platform': 'health-platform', 'SaaS with AI': 'saas-with-ai', 'Whole estate': 'whole-estate' }
// What a person may do inside a project, in the words of spec 0A, and the permission behind each.
const ACTIONS = [['edit_model', 'Edit the canvas'], ['triage', 'Triage'], ['approve', 'Approve fixes'], ['manage_members', 'Manage members'], ['read_project_audit', 'Read the audit trail']]
// Spec 0A, read for each demo person: the projects they open, and the actions they have in each.
const PEOPLE = [
  ['ceo@acme.example', 'Account owner, Approver in every project', ALL, ['triage', 'approve', 'read_project_audit']],
  ['ciso@acme.example', 'Security lead', ALL, ['triage', 'approve', 'read_project_audit']],
  ['analyst@acme.example', 'Engineer in every project', ALL, ['edit_model', 'triage']],
  ['engineer@acme.example', 'Engineer in Fintech shop', ['Fintech shop'], ['edit_model', 'triage']],
  ['health.engineer@acme.example', 'Engineer in Health platform', ['Health platform'], ['edit_model', 'triage']],
  ['auditor@acme.example', 'Auditor', ALL, ['read_project_audit']],
  ['approver@acme.example', 'Approver in Fintech shop', ['Fintech shop'], ['triage', 'approve']],
]
// Projects with a sample fix waiting, so the Approvals screen shows the Approve control or the reason it is off.
const WITH_FIXES = ['Fintech shop', 'Health platform', 'SaaS with AI']
const NOT_APPROVER = 'Only Approvers in this project can approve.'

async function signInOnHome(page, baseURL, email) {
  await page.goto(baseURL)
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(DEMO_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('heading', { level: 1, name: 'Projects' }).waitFor()
}

/** The server's answer for this person, read the way the screens read it. */
// Sent outside the page, so a refusal checked on purpose is not counted as a page error by the gate.
async function asPerson(page, url) {
  const token = await page.evaluate(() => localStorage.getItem('wh.token'))
  const response = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } })
  return { status: response.status(), body: response.ok() ? await response.json() : null }
}

async function openedProjects(page) {
  const cards = page.locator('.project-grid .project-card b')
  await expect(cards.first()).toBeVisible()
  return (await cards.allInnerTexts()).map((text) => text.slice('Open '.length))
}

async function shootWidths(page, name) {
  await page.mouse.move(0, 0)  // no hover state in the evidence
  await page.evaluate(() => document.fonts.ready)
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`), fullPage: true })
    if (width === 390) {
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(overflow, `${name} scrolls sideways at 390 px`).toBeLessThanOrEqual(1)
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })
}

test('each demo person sees exactly the projects and actions spec 0A gives them', async ({ page, app }) => {
  test.setTimeout(240_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  const table = []
  for (const [email, access, projects, actions] of PEOPLE) {
    await signInOnHome(page, app.baseURL, email)
    expect(await openedProjects(page), email).toEqual(projects)
    for (const name of ALL) {
      const me = await asPerson(page, `${app.baseURL}/api/auth/me?project=${SLUG[name]}`)
      const held = ACTIONS.map(([key]) => key).filter((key) => me.body.user.permissions.includes(key))
      if (!projects.includes(name)) {
        expect(held, `${email} in ${name}`).toEqual([])
        expect((await asPerson(page, `${app.baseURL}/api/incidents?project=${SLUG[name]}`)).status, `${email} reads ${name}`).toBe(403)
        continue
      }
      expect(held, `${email} in ${name}`).toEqual(ACTIONS.map(([key]) => key).filter((key) => actions.includes(key)))
      if (WITH_FIXES.includes(name)) {
        await page.goto(`${app.baseURL}/p/${SLUG[name]}/approvals`)
        await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible()
        if (actions.includes('approve')) {
          await expect(page.getByText(NOT_APPROVER)).toHaveCount(0)
          await expect(page.getByRole('button', { name: 'Reject' }).first()).toBeEnabled()
        } else {
          await expect(page.getByText(NOT_APPROVER)).toBeVisible()
          await expect(page.getByRole('button', { name: 'Approve' })).toBeDisabled()
        }
      }
    }
    // The project switcher offers the same projects, and no other.
    await page.goto(`${app.baseURL}/p/${SLUG[projects[0]]}`)
    await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
    await expect(page.getByLabel('Project').locator('option')).toHaveText(projects)
    table.push([email, access, projects.join(', '), labels(actions)])
    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.locator('#email').waitFor()
  }
  const rows = table.map((row) => `| ${row.join(' | ')} |`)
  await writeFile(path.join(SHOTS, 'demo-access.md'), [
    '| Demo person | Access | Projects visible | Actions allowed in those projects |',
    '| --- | --- | --- | --- |',
    ...rows,
    '',
  ].join('\n'))
  gate.assertClean()
})

function labels(actions) {
  return ACTIONS.filter(([key]) => actions.includes(key)).map(([, label]) => label).join(', ') || 'Read only'
}

test('the Whole estate holds every open finding once, and the sample banner and tags stay', async ({ page, app }) => {
  test.setTimeout(180_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await signInOnHome(page, app.baseURL, 'ceo@acme.example')
  const roll = (await asPerson(page, `${app.baseURL}/api/account/overview`)).body
  const counts = roll.findings.projects
  // Every Whole estate finding is one a product project already holds, so the account counts fewer than the cards add up to.
  const all = Object.values(counts).reduce((sum, count) => sum + count, 0)
  expect(counts['whole-estate']).toBeGreaterThanOrEqual(8)
  expect(roll.findings.account).toBeLessThanOrEqual(all - counts['whole-estate'])
  await expect(page.getByText(`Findings counted once across projects: ${roll.findings.account}.`)).toBeVisible()
  const estate = page.getByRole('link', { name: 'Open Whole estate' })
  await expect(estate).toContainText(`Open findings${counts['whole-estate']}`)
  await expect(page.getByText('This workspace shows sample data. Connect a source to see your own.')).toBeVisible()
  await assertAccessible(page)
  await shootWidths(page, 'account-home')

  await estate.click()
  await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
  await expect(page.getByLabel('Project')).toHaveValue('whole-estate')
  await assertAccessible(page)
  await shootWidths(page, 'whole-estate-overview')

  await page.goto(`${app.baseURL}/p/health-platform/incidents`)
  const row = page.getByRole('row', { name: 'INC-2216' })
  await expect(row).toContainText('Public access opened on the patient reports bucket')
  await expect(row.getByText('Sample', { exact: true })).toBeVisible()
  await expect(page.getByText('This workspace shows sample data. Connect a source to see your own.')).toBeVisible()
  await assertAccessible(page)
  await shootWidths(page, 'health-incidents')

  await page.goto(`${app.baseURL}/settings`)
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
  await page.getByRole('tab', { name: 'People' }).click()
  const people = page.getByRole('table').filter({ hasText: 'Sana Qureshi' })
  await expect(people.getByRole('row', { name: 'Sana Qureshi' })).toContainText('Auditor')
  await expect(people.getByRole('row', { name: 'Vikram Shah' })).toContainText('Approver in Fintech shop')
  await expect(people.getByRole('row', { name: 'Rohan Mehta' })).toContainText('Engineer in Health platform')
  await expect(people.getByRole('row', { name: 'Anita Rao' })).toContainText('Approver in every project')
  await assertAccessible(page)
  await shootWidths(page, 'people')
  gate.assertClean()
})

test('the health engineer, by keyboard only, reaches only the Health platform and is told why Approve is off', async ({ page, app }) => {
  test.setTimeout(120_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.goto(app.baseURL)
  await tabTo(page, page.locator('#email'))
  await page.keyboard.type('health.engineer@acme.example')
  await page.keyboard.press('Tab')
  await page.keyboard.type(DEMO_PASSWORD)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Projects' })).toBeVisible()
  expect(await openedProjects(page)).toEqual(['Health platform'])
  await expect(page.getByText('Lowest project: Health platform', { exact: false })).toBeVisible()
  await assertAccessible(page)
  await shootWidths(page, 'account-home-health-engineer')

  await tabTo(page, page.getByRole('link', { name: 'Open Health platform' }))
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
  await tabTo(page, page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Approvals' }))
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Approvals' })).toBeVisible()
  await expect(page.getByText(NOT_APPROVER)).toBeVisible()
  await expect(page.getByText('Block public access to the patient reports bucket again')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve' })).toBeDisabled()
  await assertAccessible(page)
  await shootWidths(page, 'approvals-health-engineer')
  gate.assertClean()
})

test('follow-ups: old links open a permitted project, the switcher waits without the slug, incidents fit', async ({ page, app }) => {
  test.setTimeout(120_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await signInOnHome(page, app.baseURL, 'health.engineer@acme.example')
  // A fresh load of an old path, with no project chosen yet, opens the one project this person may open.
  await page.evaluate(() => sessionStorage.clear())
  await page.goto(`${app.baseURL}/incidents`)
  await expect(page).toHaveURL(`${app.baseURL}/p/health-platform/incidents`)
  await expect(page.getByRole('heading', { level: 1, name: 'Incidents' })).toBeVisible()

  // While the project list is on its way, the switcher is a placeholder, never the bare slug.
  // The list is /api/projects?project=<slug>, so the hold has to include that query.
  let release
  const held = new Promise((resolve) => { release = resolve })
  await page.route('**/api/projects*', async (route) => { await held; await route.continue() })
  await page.goto(`${app.baseURL}/p/health-platform/incidents`)
  await expect(page.getByRole('status', { name: 'Loading projects' })).toBeVisible()
  await expect(page.getByRole('option', { name: 'health-platform' })).toHaveCount(0)
  release()
  await expect(page.getByLabel('Project').locator('option')).toHaveText(['Health platform'])
  await page.unroute('**/api/projects*')

  // The incidents table fits its card at every width: it stacks below 1024 px instead of scrolling sideways.
  await expect(page.getByRole('row', { name: 'INC-2216' })).toBeVisible()
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 })
    const spill = await page.locator('.tbl-wrap').first().evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(spill, `incidents table at ${width} px`).toBeLessThanOrEqual(1)
  }
  await assertAccessible(page)
  await shootWidths(page, 'health-incidents')

  await page.getByRole('button', { name: 'Sign out' }).click()
  await signInOnHome(page, app.baseURL, 'ceo@acme.example')
  await expect(page.getByText('Whole estate: ', { exact: false })).toContainText('Gathers other projects, so it is not weighed again')
  gate.assertClean()
})
