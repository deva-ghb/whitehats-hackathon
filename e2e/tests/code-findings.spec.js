/** What a pushed change shows (VS.4): code findings on the Overview, with file and line, branch, commit and state. */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, shoot } from '../gate.js'
import { signIn } from '../sign-in.js'

const BACKEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../backend')
const REPO = 'deva-ghb/whitehats-proving-ground'
const HEAD = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'
const OPEN_TITLE = 'Provider live key in code'
const CLOSED_TITLE = 'Unsafe deserialisation of a config value'

/** Two findings for the Fintech shop: one still open, one a later commit closed. Written through the app's own models. */
function seed(dataDir) {
  execFileSync('uv', ['run', 'python', '-c', `
from datetime import timedelta
from sqlalchemy import delete, select
from app.db import SessionLocal, utcnow
from app.models import ChangeEvent, Connector, Organization, Project, Signal, VexStatement

with SessionLocal() as db:
    # Every test in a run shares one server and one database: put back what an earlier seed left.
    db.execute(delete(Signal).where(Signal.source == "whitehats:code"))
    db.execute(delete(ChangeEvent).where(ChangeEvent.repo == "${REPO}"))
    db.execute(delete(VexStatement))
    db.commit()
    org = db.scalar(select(Organization).where(Organization.slug == "acme"))
    project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
    connector = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "github"))
    change = ChangeEvent(org_id=org.id, connector_id=connector.id, project_ids=[project.id], repo="${REPO}",
                         branch="demo/S03-test", default_branch="main", head_sha="${HEAD}", first_sha="${HEAD}",
                         status="decided", verdict="suspect", check_url="https://github.com/${REPO}/runs/42")
    db.add(change)
    db.flush()
    url = "https://github.com/${REPO}"
    base = dict(org_id=org.id, project_id=project.id, source="whitehats:code", origin="live", resource=url,
                branch="demo/S03-test", change_id=change.id)
    db.add(Signal(severity="high", title="${OPEN_TITLE}", entity="shop/settings.py:12", fingerprint="r|shop/settings.py|",
                  detail="shop/settings.py:12 (CWE-798). A live key is committed to the repository.\\nCommit: x",
                  created_at=utcnow(), **base))
    db.add(Signal(severity="critical", title="${CLOSED_TITLE}", entity="shop/promo_config.py:11",
                  fingerprint="s|shop/promo_config.py|", detail="shop/promo_config.py:11. Untrusted data is loaded as code.\\nCommit: y",
                  created_at=utcnow() - timedelta(hours=3), closed_at=utcnow(), closed_reason="fixed by a later commit", **base))
    db.commit()
`], { cwd: BACKEND, env: { ...process.env, WH_DATA_DIR: dataDir, WH_LOAD_DOTENV: '0' }, stdio: 'pipe' })
}

test('pushed code findings show their file and line, branch, commit and whether a later commit closed them', async ({ page, app }) => {
  const gate = attachGate(page)
  seed(app.dataDir)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/p/main`)

  const card = page.locator('.card', { has: page.getByRole('heading', { name: 'Code findings' }) })
  await expect(card).toBeVisible()
  const open = card.locator('.code-finding[data-state="open"]')
  const closed = card.locator('.code-finding[data-state="closed"]')

  await expect(open).toHaveCount(1)
  await expect(open).toContainText(OPEN_TITLE)
  await expect(open).toContainText('Open')
  await expect(open).toContainText('shop/settings.py:12')
  await expect(open).toContainText('demo/S03-test')
  await expect(open).toContainText(REPO)
  await expect(open).toContainText('A live key is committed to the repository.')
  await expect(open.getByRole('link', { name: HEAD.slice(0, 7) })).toHaveAttribute('href', `https://github.com/${REPO}/commit/${HEAD}`)
  await expect(open.getByRole('link', { name: 'WhiteHats check on GitHub' })).toHaveAttribute('href', `https://github.com/${REPO}/runs/42`)

  await expect(closed).toHaveCount(1)
  await expect(closed).toContainText(CLOSED_TITLE)
  await expect(closed).toContainText('Closed: fixed by a later commit')  // said in words, not only by the strike-through

  // Findings have their own card: they are not repeated among the latest signals.
  await expect(page.locator('.signals-card')).not.toContainText(OPEN_TITLE)

  await assertAccessible(page)
  await shoot(page, 'code-findings', { mask: [page.locator('.num'), page.locator('.signal-source'), page.locator('.code-finding-where')] })
  gate.assertClean()
})

test('a closed finding is not offered when someone opens an incident', async ({ page, app }) => {
  seed(app.dataDir)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/incidents`)
  await page.getByRole('button', { name: 'Open an incident' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText(OPEN_TITLE)
  await expect(dialog).not.toContainText(CLOSED_TITLE)
})

test('an engineer marks a finding not affected: it stays listed with the reason and name, goes into the VEX, and withdrawing opens it again', async ({ page, app }) => {
  const gate = attachGate(page)
  seed(app.dataDir)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  await page.goto(`${app.baseURL}/p/main`)
  const card = page.locator('.card', { has: page.getByRole('heading', { name: 'Code findings' }) })
  const finding = card.locator('.code-finding', { hasText: OPEN_TITLE })
  await expect(card.locator('.code-finding[data-state="closed"]').getByRole('button')).toHaveCount(0)  // nothing to mark

  await finding.getByRole('button', { name: 'Not affected…' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('The finding stays listed, marked not affected')
  await dialog.getByRole('combobox', { name: 'Why does it not apply?' }).click()
  await page.getByRole('option', { name: 'Something else already stops it' }).click()
  await expect(dialog.getByRole('button', { name: 'Record it' })).toBeDisabled()  // a decision needs a reason
  await dialog.getByLabel('In your own words').fill('the key is a sandbox key the provider rejects in production')
  await assertAccessible(page)
  await dialog.getByRole('button', { name: 'Record it' }).click()

  await expect(finding).toHaveAttribute('data-state', 'not-affected')
  await expect(finding.locator('.code-finding-state')).toHaveText('Not affected')
  await expect(finding).toContainText('Not affected: something else already stops it. the key is a sandbox key the provider rejects in production (decided by')
  const vex = await page.evaluate(async () => (await fetch('/api/vex.json?project=main', {
    headers: { Authorization: `Bearer ${localStorage.getItem('wh.token')}` } })).json())
  expect(vex['@context']).toBe('https://openvex.dev/ns/v0.2.0')
  expect(vex.statements.map((item) => [item.status, item.justification])).toEqual([['not_affected', 'inline_mitigations_already_exist']])
  await assertAccessible(page)
  await expect(page.getByText('Recorded as not affected')).toHaveCount(0, { timeout: 15_000 })  // the toast has gone
  await page.evaluate(() => window.scrollTo(0, 0))  // a full-page picture is taken from the top
  await shoot(page, 'code-finding-not-affected', { mask: [page.locator('.num'), page.locator('.signal-source'), page.locator('.code-finding-where'), page.locator('.code-finding-vex')] })

  await finding.getByRole('button', { name: 'Withdraw “not affected”' }).click()
  await expect(finding).toHaveAttribute('data-state', 'open')
  await expect(finding.locator('.code-finding-state')).toHaveText('Open')
  gate.assertClean()
})

test.afterAll(async ({ app }) => {
  execFileSync('uv', ['run', 'python', '-c', `
from sqlalchemy import delete
from app.db import SessionLocal
from app.models import ChangeEvent, Signal, VexStatement

with SessionLocal() as db:
    db.execute(delete(Signal).where(Signal.source == "whitehats:code"))
    db.execute(delete(ChangeEvent).where(ChangeEvent.repo == "${REPO}"))
    db.execute(delete(VexStatement))
    db.commit()
`], { cwd: BACKEND, env: { ...process.env, WH_DATA_DIR: app.dataDir, WH_LOAD_DOTENV: '0' }, stdio: 'pipe' })
})
