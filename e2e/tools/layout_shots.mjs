// STUDIO.5: opens each real map with its stored places stripped, in the Plan and the Scene at 1440 and 1920, and saves the screenshots.
// Its own app on its own database. Usage: node tools/layout_shots.mjs <output folder> [map names...]
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'
import { estate500 } from '../estate-500.js'
import { startApp } from '../fixtures.js'
import { signIn } from '../sign-in.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../..')
const OUT = path.resolve(process.argv[2])
const ONLY = process.argv.slice(3)
const readJson = async (file) => JSON.parse(await readFile(path.join(ROOT, file), 'utf8'))

// The examples name the proving ground's vault; the demo company's own is the one the server accepts.
const stripPlaces = (document) => JSON.parse(JSON.stringify({
  ...document, nodes: document.nodes.map(({ layout, ...node }) => node), groups: document.groups.map(({ layout, ...group }) => group),
}).replaceAll('vault://tenant/proving-ground/', 'vault://tenant/acme/').replaceAll('vault://tenant/preventive/', 'vault://tenant/acme/').replaceAll('vault://tenant/fintech/', 'vault://tenant/acme/'))
export const MAPS = {
  'by-hand': async () => stripPlaces(await readJson('platform/e2e/screenshots/P2.5/proving-ground-by-hand.clone.json')),
  'sample-pg': async () => stripPlaces(await readJson('library/examples/proving-ground.clone.json')),
  'template-a': async () => stripPlaces(await readJson('library/examples/template-a.clone.json')),
  'template-b': async () => stripPlaces(await readJson('library/examples/template-b.clone.json')),
  'big-500': async () => stripPlaces(estate500('big-500')),
}
const SIZES = [[1440, 900], [1920, 1080]]

await mkdir(OUT, { recursive: true })
const app = await startApp()
const browser = await chromium.launch()
try {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage()
  const problems = []
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`) })
  await signIn(page, app.baseURL, 'engineer@acme.example', 'plan')
  const names = Object.keys(MAPS).filter((name) => !ONLY.length || ONLY.includes(name))
  for (const name of names) {
    const document = { ...(await MAPS[name]()), clone: name }
    const status = await page.evaluate(async (body) => {
      const token = localStorage.getItem('wh.token')
      const project = location.pathname.split('/')[2]
      const response = await fetch(`/api/clones?project=${project}`, { method: 'POST', body: JSON.stringify(body), headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } })
      return `${response.status} ${response.status === 200 ? '' : await response.text()}`
    }, { reason: 'Layout check', document })
    if (!status.startsWith('200')) throw new Error(`${name}: ${status}`)
  }
  for (const name of names) {
    for (const view of ['plan', 'scene']) {
      for (const [width, height] of SIZES) {
        await page.setViewportSize({ width, height })
        await page.evaluate((choice) => localStorage.setItem('wh-studio-view', choice), view)
        await page.goto(`${app.baseURL}/studio`)
        await page.getByRole('toolbar', { name: 'Clone' }).waitFor()
        const picker = page.getByRole('combobox', { name: 'Clone' })
        if (await picker.waitFor({ timeout: 5000 }).then(() => true, () => false)) {
          await picker.click()
          await page.getByRole('option', { name }).first().click()
        }
        await page.getByRole('toolbar', { name: 'Clone' }).getByText(name, { exact: true }).waitFor()
        await page.waitForTimeout(name === 'big-500' ? 4000 : 1800)
        console.log('open:', (await page.getByRole('toolbar', { name: 'Clone' }).innerText()).replaceAll('\n', ' | '))
        const zoom = view === 'plan' ? await page.locator('.react-flow__viewport').evaluate((el) => el.style.transform) : ''
        await page.screenshot({ path: path.join(OUT, `${name}-${view}-${width}.png`) })
        console.log(name, view, width, zoom)
      }
    }
  }
  console.log('problems:', JSON.stringify(problems))
} finally {
  await browser.close()
  await app.stop()
}
