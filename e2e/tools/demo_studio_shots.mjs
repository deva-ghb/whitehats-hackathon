// The Studio as the demo shows it, on this machine: a fake AWS account the size of the demo company's (moto, never real
// AWS), connected from the Studio, then the map saved at the demo recorder's window and at 1440 and 1920.
// Usage: node tools/demo_studio_shots.mjs <output folder> [--select <node label>]
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'
import { startFakeAws } from '../fake-aws.js'
import { startApp } from '../fixtures.js'
import { signIn } from '../sign-in.js'

const OUT = path.resolve(process.argv[2])
const SIZES = [[1440, 810, 'demo'], [1440, 900, '1440'], [1920, 1080, '1920']]

await mkdir(OUT, { recursive: true })
process.env.FAKE_AWS_ESTATE = 'proving-ground'
const aws = await startFakeAws()
const app = await startApp({ ...aws.env, WH_STUDIO_HAND_EDITING: '0' })
const browser = await chromium.launch()
try {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 1 })).newPage()
  const problems = []
  page.on('console', (m) => { if (m.type() === 'error') problems.push(m.text()) })
  await signIn(page, app.baseURL, 'ceo@acme.example', 'plan')
  await page.goto(`${page.url().split('/p/')[0]}/p/main/studio`)
  await page.getByRole('button', { name: 'Connect a source' }).click()
  await page.getByRole('dialog', { name: 'Connect a source' }).getByRole('button', { name: /^AWS Your services/ }).click()
  const setup = page.getByRole('complementary', { name: 'Inspector' }).getByRole('region', { name: 'AWS connection' })
  await setup.locator('#connection-role_arn').fill(aws.role_arn)
  await setup.locator('#connection-external_id').fill('e2e-external-id')
  await setup.locator('#connection-region').fill(aws.region)
  await setup.getByRole('button', { name: 'Connect' }).click()
  await setup.getByTestId('connection-found').waitFor({ timeout: 60_000 })
  await page.waitForFunction(() => document.querySelectorAll('[data-scene-item]').length > 15, null, { timeout: 60_000 })
  await page.waitForTimeout(1500)
  for (const [width, height, name] of SIZES) {
    await page.setViewportSize({ width, height })
    await page.waitForTimeout(1200)
    await page.screenshot({ path: path.join(OUT, `connected-${name}.png`) })
  }
  // The same map after a reload, nothing selected: what a person sees coming back to it.
  await page.setViewportSize({ width: 1440, height: 810 })
  await page.reload()
  await page.getByRole('application', { name: /Isometric scene/ }).waitFor()
  await page.waitForTimeout(2000)
  for (const [width, height, name] of SIZES) {
    await page.setViewportSize({ width, height })
    await page.waitForTimeout(1200)
    await page.screenshot({ path: path.join(OUT, `reopened-${name}.png`) })
  }
  // An open cloud alert on the receipts bucket, as Cloud Posture raises one: the building turns red within 15 seconds.
  if (process.argv.includes('--alert')) {
    const { execFileSync } = await import('node:child_process')
    execFileSync('uv', ['run', 'python', '-c', `
from app.db import SessionLocal
from app.models import Organization, Project, Signal
db = SessionLocal()
org = db.query(Organization).filter(Organization.slug == "acme").one()
p = db.query(Project).filter(Project.org_id == org.id, Project.slug == "main").one()
db.add(Signal(org_id=p.org_id, project_id=p.id, severity="medium", title="Public access settings on a bucket changed",
              resource="whitehats-proving-ground-509594260364-receipts", source="whitehat:cloud",
              detail="PutBucketPublicAccessBlock on whitehats-proving-ground-509594260364-receipts by dev."))
db.commit()`], { cwd: path.resolve('../backend'), env: { ...process.env, WH_DATA_DIR: app.dataDir, WH_LOAD_DOTENV: '0' } })
    await page.waitForTimeout(17_000)
    console.log('alerts api:', JSON.stringify(await page.evaluate(async () => {
      const headers = { Authorization: `Bearer ${localStorage.getItem('wh.token')}` }
      const list = await (await fetch('/api/clones?project=main', { headers })).json()
      const answer = await fetch(`/api/clones/${list.clones[0].id}/alerts?project=main`, { headers })
      return { status: answer.status, body: await answer.json() }
    })).slice(0, 900))
    await page.screenshot({ path: path.join(OUT, 'alert-demo.png') })
    console.log('alert placed; red solids:', await page.locator('.sc-solid.c, [data-tone="c"]').count())
  }
  console.log('items:', await page.locator('[data-scene-item]').count(), 'problems:', JSON.stringify(problems))
} finally {
  await browser.close()
  await app.stop()
  await aws.stop()
}
