// The knowledge sphere (KS.1) on this machine, with a shop's sphere seeded as approvers' decisions would build it:
// proposals from the first audit, some confirmed, a fix's rule, an accepted exception, and a rule forgotten when its
// code went. Needs a PostgreSQL with Apache AGE (WH_SPHERE_DATABASE_URL, default the local development one).
// Usage: node tools/sphere_shots.mjs <output folder>
import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'
import { startApp } from '../fixtures.js'
import { signIn } from '../sign-in.js'

const OUT = path.resolve(process.argv[2])
const SPHERE_DB = process.env.WH_SPHERE_DATABASE_URL || 'postgresql://whitehats:whitehats-dev@localhost:5455/sphere'
await mkdir(OUT, { recursive: true })
const app = await startApp({ WH_SPHERE_DATABASE_URL: SPHERE_DB })

const SEED = `
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Organization, Project, SphereGraphState, SphereVersion
from app.sphere import store, graph
db = SessionLocal()
org = db.scalar(select(Organization).where(Organization.slug == "acme"))
p = db.scalar(select(Project).where(Project.org_id == org.id, Project.slug == "main"))
db.query(SphereVersion).filter(SphereVersion.project_id == p.id).delete()
db.query(SphereGraphState).filter(SphereGraphState.project_id == p.id).delete()
db.commit()
def f(path, a, b): return {"kind": "file", "path": path, "lines": [a, b]}
def dec(label, by="Anita Rao"): return {"kind": "decision", "ref": label, "by": by, "at": "2026-11-09T10:00:00", "label": label}
def node(key, kind, text, status="proposed", cites=(), **kw):
    return {"op": "put_node", "node": {"key": key, "kind": kind, "text": text, "status": status, "cites": list(cites),
            "lost": [], "taught_by": [], **kw}}
def edge(kind, a, b): return {"op": "put_edge", "edge": {"kind": kind, "source": a, "target": b, "taught_by": []}}
C = [("concept:order", "Order"), ("concept:price", "Price"), ("concept:customer", "Customer"),
     ("concept:receipt", "Receipt"), ("concept:payment-job", "Payment job")]
R = [("rule:price-from-catalogue", "An order's price comes from the catalogue, never from the request", "money", "concept:order", f("services/orders/app/handler.py", 38, 52)),
     ("rule:own-orders", "A customer sees only their own orders", "data", "concept:customer", f("services/orders/app/handler.py", 60, 74)),
     ("rule:receipt-private", "Receipts are never readable by anyone outside the company", "data", "concept:receipt", {"kind": "node", "arn": "arn:aws:s3:::whitehats-proving-ground-509594260364-receipts", "name": "receipts bucket"}),
     ("rule:refund-needs-order", "A refund needs a paid order of the same customer", "money", "concept:payment-job", f("services/payments-worker/app/worker.py", 20, 41)),
     ("rule:payment-once", "A payment job charges a card at most once", "money", "concept:payment-job", f("services/payments-worker/app/worker.py", 55, 80)),
     ("rule:discount-cap", "A discount never takes an order below cost", "money", "concept:price", f("services/orders/app/pricing.py", 10, 24))]
ops = [node(k, "Concept", t) for k, t in C]
for k, t, protects, c, cite in R:
    ops += [node(k, "Rule", t, cites=[cite], protects=protects), edge("ABOUT", k, c)]
ops += [edge("RELATES", "concept:order", "concept:price"), edge("RELATES", "concept:order", "concept:customer"),
        edge("RELATES", "concept:order", "concept:receipt"), edge("RELATES", "concept:payment-job", "concept:order")]
store.record(db, org.id, p.id, ops, actor="WhiteHats", reason="The onboarding audit proposed what the business depends on")
now = store.state(db, p.id)
keep = ["concept:order", "concept:price", "concept:customer", "concept:receipt", "rule:price-from-catalogue", "rule:own-orders", "rule:receipt-private"]
store.record(db, org.id, p.id, [{"op": "put_node", "node": {**now.nodes[k], "status": "known", "taught_by": ["confirm:1"],
             "cites": now.nodes[k]["cites"] + [dec("Confirmed by Anita Rao")]}} for k in keep],
             actor="Anita Rao", reason="Anita Rao confirmed 7 proposals")
store.record(db, org.id, p.id, [node("exception:free-samples", "Exception", "Not a problem here: free sample orders are recorded at zero",
             status="known", cites=[f("services/orders/app/handler.py", 88, 96), {"kind": "finding", "id": 1, "title": "Sample orders cost nothing"}, dec("Marked not affected by Anita Rao")],
             detail="Samples are free by design and capped at one per customer.", taught_by=["vex:3"]),
             edge("EXEMPTS", "exception:free-samples", "rule:price-from-catalogue")],
             actor="Anita Rao", reason="Anita Rao marked a finding not affected: WhiteHats accepted it as an exception")
store.record(db, org.id, p.id, [{"op": "drop_node", "key": "rule:discount-cap"}], actor="WhiteHats",
             reason="What 1 item cited is gone from the code or the account")
db.commit()
print("synced", store.sync_graph(db, p.id))
`
execFileSync('uv', ['run', 'python', '-c', SEED], { cwd: path.resolve('../backend'), stdio: 'inherit',
  env: { ...process.env, WH_DATA_DIR: app.dataDir, WH_LOAD_DOTENV: '0', WH_SPHERE_DATABASE_URL: SPHERE_DB } })

const browser = await chromium.launch()
try {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage()
  const problems = []
  page.on('console', (m) => { if (m.type() === 'error') problems.push(m.text()) })
  await signIn(page, app.baseURL, 'ceo@acme.example', 'plan')
  await page.goto(`${page.url().split('/p/')[0]}/p/main/sphere`)
  const rule = page.getByRole('button', { name: /Business rule: An order's price/ })
  await rule.waitFor({ state: 'attached' })
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(OUT, 'globe-entering-1440.png') })
  await page.waitForTimeout(1600)
  await page.screenshot({ path: path.join(OUT, 'globe-1440.png'), fullPage: true })
  const box = await page.locator('.globe').boundingBox()
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.4, { steps: 12 })
  await page.mouse.up()
  await page.waitForTimeout(700)
  await page.screenshot({ path: path.join(OUT, 'globe-dragged-1440.png') })
  await rule.focus()
  await page.waitForTimeout(1500)
  await rule.click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: path.join(OUT, 'globe-selected-1440.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(OUT, 'globe-390.png'), fullPage: true })
  console.log('problems:', JSON.stringify(problems))
} finally {
  await browser.close()
  await app.stop()
}
