/** SC.1: a service node with a known package or image weakness shows a badge coloured by its reach, and the inspector
 *  lists the weaknesses most urgent first, saying why in one line with the path named; an image's rows name the image,
 *  and past its worst ten say how many more. At 1440 and 390 wide. */

import { execFileSync } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { assertAccessible, attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = path.resolve(HERE, '../screenshots/SC.1')
const BACKEND = path.resolve(HERE, '../../backend')
const SLUG = 'weak-packages'

// Its own server: it adds an advisory to the knowledge base and a clone other specs must not open.
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

const BASE = 'public.ecr.aws/lambda/python:3.9'

/** One advisory in the server's knowledge base, written the way a refresh writes it (no network), and a stored image
 *  scan of the worker's base image with 12 fixable weaknesses, written the way the image worker writes one. */
function addAdvisory(dataDir) {
  const record = {
    id: 'PYSEC-2026-0501', modified: '2026-10-30T09:00:00Z', published: '2026-10-01T00:00:00Z', aliases: ['CVE-2026-0501'],
    summary: 'Loading untrusted YAML runs code.',
    affected: [{ package: { ecosystem: 'PyPI', name: 'PyYAML' }, ranges: [{ type: 'ECOSYSTEM', events: [{ introduced: '0' }, { fixed: '5.4' }] }] }],
    database_specific: { severity: 'HIGH' },
  }
  execFileSync('uv', ['run', 'python', '-c', `
import json, sys
from app.db import SessionLocal
from app.knowledge import osv
from datetime import datetime
from app.models import ImageScan
with SessionLocal() as db:
    osv.store(db, "PyPI", json.loads(sys.argv[1]))
    found = [{"image": sys.argv[2], "digest": "", "package": f"lib{n:02d}", "installed": "1.0", "fixed": "1.1",
              "advisory": f"CVE-2026-{1000 + n}", "severity": "high" if n < 2 else "medium", "title": f"lib{n:02d}: a flaw",
              "cwe": []} for n in range(12)]
    db.add(ImageScan(image=sys.argv[2], scanned_at=datetime(2026, 10, 31), weaknesses=found, unfixed=0))
    db.commit()
`, JSON.stringify(record), BASE], { cwd: BACKEND, env: { ...process.env, WH_DATA_DIR: dataDir, WH_LOAD_DOTENV: '0' } })
}

async function makeClone(page) {
  return page.evaluate(async ({ slug, base }) => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('wh.token')}`, 'Content-Type': 'application/json' }
    const weak = { packages: ['PyPI:pyyaml==5.3.1'] }
    const document = {
      schema_version: 1, clone: slug, environment: 'prod', library_version: '2026.10',
      version: { number: 1, author: 'Test', reason: 'SC.1' }, groups: [], access_paths: [], credential_refs: [],
      nodes: [
        { id: 'api', type: 'edge.api-gateway@1', name: 'public API', environment: 'prod' },
        { id: 'shop', type: 'service.function@2', name: 'shop-api', environment: 'prod', properties: weak },
        { id: 'report', type: 'service.function@2', name: 'nightly-report', environment: 'prod', properties: weak },
        { id: 'worker', type: 'service.function@2', name: 'payments-worker', environment: 'prod', properties: { base_image: base } },
        { id: 'customers', type: 'data.dynamodb@1', name: 'customers', environment: 'prod', criticality: 'crown-jewel' },
      ],
      edges: [{ from: 'api', to: 'shop', type: 'calls' }, { from: 'shop', to: 'customers', type: 'reads' },
        { from: 'api', to: 'worker', type: 'calls' }],
    }
    const res = await fetch('/api/clones?project=main', { method: 'POST', headers, body: JSON.stringify({ reason: 'SC.1', document }) })
    return (await res.json()).id
  }, { slug: SLUG, base: BASE })
}

const card = (page, name) => page.locator('.react-flow__node-estate').filter({ has: page.getByText(name, { exact: true }) })

test('a weak package shows on its node by reach, and the inspector says why, naming the path', async ({ page, app }) => {
  const gate = attachGate(page)
  addAdvisory(app.dataDir)
  await signIn(page, app.baseURL, 'engineer@acme.example')
  const id = await makeClone(page)
  await page.goto(`${app.baseURL}/studio?clone=${id}`)

  const shop = card(page, 'shop-api')
  await expect(shop.getByText('1 weakness')).toBeVisible()
  await expect(shop.locator('.estate-state.weak.reach-1')).toBeVisible()  // on the path to the customers table
  await expect(card(page, 'nightly-report').locator('.estate-state.weak.reach-3')).toBeVisible()  // nothing reaches it
  await expect(card(page, 'public API').locator('.estate-state.weak')).toHaveCount(0)

  await shop.click()
  const list = page.getByRole('complementary', { name: 'Inspector' }).getByRole('region', { name: 'Known weaknesses' })
  await expect(list.getByText('public API, then shop-api, then customers.')).toBeVisible()
  await expect(list.locator('.estate-state.weak.reach-1')).toHaveText('On an attack path')
  await expect(list.getByRole('link', { name: 'PYSEC-2026-0501' })).toHaveAttribute('href', 'https://osv.dev/vulnerability/PYSEC-2026-0501')
  await expect(list.getByText('pyyaml 5.3.1: fixed in 5.4')).toBeVisible()
  await expect(list.getByText('Vulnerability data from OSV.dev')).toBeVisible()
  await assertAccessible(page)

  // An image's weaknesses: the worker is reachable from the API but on no path to the customers table.
  const worker = card(page, 'payments-worker')
  await expect(worker.getByText('12 weaknesses')).toBeVisible()
  await expect(worker.locator('.estate-state.weak.reach-2')).toBeVisible()
  await worker.click()
  await expect(list.getByRole('listitem')).toHaveCount(10)
  await expect(list.getByText('Image public.ecr.aws/lambda/python:3.9')).toBeVisible()
  await expect(list.getByText('lib00 1.0, in the image: fixed in 1.1')).toBeVisible()
  await expect(list.getByText('And 2 more in the image.')).toBeVisible()
  await expect(list.getByText('Image weaknesses found by Trivy')).toBeVisible()
  await expect(list.getByText('Vulnerability data from OSV.dev')).toHaveCount(0)  // no package rows on this node
  await assertAccessible(page)
  await mkdir(SHOTS, { recursive: true })
  await list.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(SHOTS, 'image-weaknesses-1440.png') })
  await shop.click()

  await mkdir(SHOTS, { recursive: true })
  // The plan view has one theme (only the scene has a dark one), so one set of pictures.
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width < 800 ? 844 : 900 })
    if (width === 390 && !(await list.isVisible())) await shop.click()
    await expect(list).toBeVisible()
    await list.scrollIntoViewIfNeeded()
    await assertAccessible(page)
    await page.screenshot({ path: path.join(SHOTS, `weaknesses-${width}.png`) })
  }
  gate.assertClean()
})
