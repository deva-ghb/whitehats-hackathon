/** FX.1: a fix has its own page: what was found, what will change and what it touches, before anyone decides. */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { test } from '../fixtures.js'
import { assertAccessible, attachGate, shoot, tabTo } from '../gate.js'
import { signIn } from '../sign-in.js'

const BACKEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../backend')
const REPO = 'deva-ghb/whitehats-proving-ground'
const HEAD = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'
const FIX = 'Stop naming receipts by their storage key'

function python(dataDir, code) {
  return execFileSync('uv', ['run', 'python', '-c', code], {
    cwd: BACKEND, env: { ...process.env, WH_DATA_DIR: dataDir, WH_LOAD_DOTENV: '0' }, stdio: 'pipe' }).toString().trim()
}

/** One confirmed code finding on a pushed change, and a Fix Factory fix proposed from it. Returns the fix's id. */
function seed(dataDir) {
  return python(dataDir, `
from sqlalchemy import delete, select
from app.db import SessionLocal, utcnow
from app.models import ChangeEvent, Connector, Organization, Project, Remediation, Signal

SUSPECT = {"engine": "house-rules", "rule_id": "whitehats.storage-key", "title": "Caller value used as an object key",
           "evidence": {"path": "shop/receipts.py", "start_line": 34, "end_line": 34, "resource": ""},
           "severity": "high", "cwe": ["CWE-22"], "status": "open", "note": ""}
with SessionLocal() as db:
    db.execute(delete(Remediation).where(Remediation.title == "${FIX}"))
    db.execute(delete(Signal).where(Signal.source == "whitehats:code"))
    db.execute(delete(ChangeEvent).where(ChangeEvent.repo == "${REPO}"))
    db.commit()
    org = db.scalar(select(Organization).where(Organization.slug == "acme"))
    project = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
    connector = db.scalar(select(Connector).where(Connector.org_id == org.id, Connector.key == "github"))
    change = ChangeEvent(org_id=org.id, connector_id=connector.id, project_ids=[project.id], repo="${REPO}",
                         branch="demo/S02", default_branch="main", head_sha="${HEAD}", first_sha="${HEAD}",
                         status="decided", verdict="suspect", check_url="https://github.com/${REPO}/runs/42",
                         confirmations=[{"suspect": SUSPECT, "status": "confirmed", "severity": "high", "model": "luna",
                                         "explanation": "Any signed-in customer can name another customer's receipt and read it."}])
    db.add(change)
    db.flush()
    finding = Signal(org_id=org.id, project_id=project.id, severity="high", title=SUSPECT["title"],
                     entity="shop/receipts.py:34", fingerprint="whitehats.storage-key|shop/receipts.py|",
                     detail="shop/receipts.py:34 (CWE-22). Any signed-in customer can read another customer's receipt.",
                     resource="https://github.com/${REPO}", source="whitehats:code", origin="live", branch="demo/S02",
                     change_id=change.id, created_at=utcnow())
    db.add(finding)
    db.flush()
    fix = Remediation(org_id=org.id, project_id=project.id, signal_id=finding.id, title="${FIX}", target="shop-api",
                      action="Look receipts up by the signed-in customer's own order, never by a name the caller sends.",
                      why="A confirmed high finding on shop/receipts.py.", destination="Fix Factory", risk="high")
    db.add(fix)
    db.commit()
    print(fix.id)
`)
}

test('a fix opens on its own page with the finding, what will change and what it touches', async ({ page, app }) => {
  const gate = attachGate(page)
  const id = seed(app.dataDir)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/p/main/approvals`)

  const card = page.locator('.card', { hasText: FIX })
  await tabTo(page, card.getByRole('link', { name: 'See details' }))  // reachable without a mouse
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/approvals/${id}$`))

  await expect(page.getByRole('heading', { level: 2, name: FIX })).toBeVisible()
  const found = page.locator('.card', { has: page.getByRole('heading', { name: 'What was found' }) })
  await expect(found).toContainText('Caller value used as an object key')
  await expect(found).toContainText(`shop/receipts.py:34 on demo/S02 in ${REPO} · commit ${HEAD.slice(0, 7)}`)
  await expect(found).toContainText('house-rules · rule whitehats.storage-key · CWE-22')
  await expect(found).toContainText("The AI model confirmed it (luna): Any signed-in customer can name another customer's receipt and read it.")
  await expect(found.getByRole('link', { name: 'WhiteHats check on GitHub' })).toHaveAttribute('href', `https://github.com/${REPO}/runs/42`)
  await expect(found).toContainText('Code not shown:')  // this run has no GitHub to read from, and the page says so

  const change = page.locator('.card', { has: page.getByRole('heading', { name: 'What will change' }) })
  await expect(change).toContainText('Fix Factory has not worked on this yet')
  await expect(change).toContainText("never by a name the caller sends")
  await expect(change).toContainText("writes a check that fails on today's code")

  await expect(page.locator('.card', { has: page.getByRole('heading', { name: 'What it touches' }) })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apply Fix' })).toBeVisible()

  await assertAccessible(page)
  await shoot(page, 'fix-details', { mask: [page.locator('.fix-facts .mono')] })
  gate.assertClean()
})

test('a sample fix from an incident shows the incident\'s signals and the steps a person carries out', async ({ page, app }) => {
  const gate = attachGate(page)
  await signIn(page, app.baseURL)
  await page.goto(`${app.baseURL}/p/main/approvals`)
  const card = page.locator('.card', { hasText: 'Remove the policy the root account attached' }).first()
  await card.getByRole('link', { name: 'Remove the policy the root account attached' }).click()

  await expect(page.locator('.card', { has: page.getByRole('heading', { name: 'What was found' }) })).toContainText('From incident')
  const change = page.locator('.card', { has: page.getByRole('heading', { name: 'What will change' }) })
  await expect(change).toContainText('A person carries this out in Slack')
  await expect(change.locator('ol li').first()).toBeVisible()
  await page.getByRole('link', { name: '← All approvals' }).click()
  await expect(page).toHaveURL(/\/approvals$/)
  gate.assertClean()
})

test.afterAll(async ({ app }) => {
  python(app.dataDir, `
from sqlalchemy import delete
from app.db import SessionLocal
from app.models import ChangeEvent, Remediation, Signal

with SessionLocal() as db:
    db.execute(delete(Remediation).where(Remediation.title == "${FIX}"))
    db.execute(delete(Signal).where(Signal.source == "whitehats:code"))
    db.execute(delete(ChangeEvent).where(ChangeEvent.repo == "${REPO}"))
    db.commit()
`)
})
