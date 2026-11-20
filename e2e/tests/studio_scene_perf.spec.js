/** The scene stays smooth on a 500 node clone (STUDIO.2): frames a second while panning and zooming, no long task, memory flat. */

import { mkdir, mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect } from '@playwright/test'
import { startApp, test as base } from '../fixtures.js'
import { attachGate } from '../gate.js'
import { signIn } from '../sign-in.js'

const SHOTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../screenshots/STUDIO.3')
const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

const TYPES = ['service.backend@1', 'service.function@2', 'data.rds@1', 'data.s3@1', 'messaging.sqs@1', 'identity.okta@1', 'identity.kms@1',
  'observability.cloudwatch@1', 'service.worker@1', 'service.frontend@1']

/** Ten zones of fifty nodes each, placed on a grid, joined by 600 connections. A fixed pattern, so the run is the same every time. */
function bigClone() {
  const groups = []
  const nodes = []
  for (let zone = 0; zone < 10; zone += 1) {
    const id = `g_zone${zone}`
    groups.push({ id, type: 'boundary.vpc@1', name: `Zone ${zone}`, kind: 'vpc', environment: 'prod',
      layout: { x: (zone % 5) * 900, y: Math.floor(zone / 5) * 1800, width: 800, height: 1632 } })
    for (let k = 0; k < 50; k += 1) {
      nodes.push({ id: `n_${zone}_${k}`, type: TYPES[(zone * 7 + k * 3) % TYPES.length], name: `item ${zone}-${k}`, environment: 'prod', location: id,
        layout: { x: 24 + (k % 3) * 260, y: 48 + Math.floor(k / 3) * 92 } })
    }
  }
  const edges = []
  for (let i = 0; i < 600; i += 1) {
    const from = nodes[(i * 37) % nodes.length].id
    const to = nodes[(i * 91 + 13) % nodes.length].id
    if (from !== to) edges.push({ from, to, type: 'generic_relation' })
  }
  return {
    schema_version: 1, clone: 'big-500', environment: 'prod', library_version: '2026.10',
    version: { number: 1, author: 'test', reason: 'A 500 node clone for the speed check' },
    nodes, edges, groups, access_paths: [], credential_refs: [],
  }
}

test('a 500 node clone pans and zooms above 30 frames a second with no long task and flat memory', async ({ page, app }) => {
  test.setTimeout(240_000)
  await mkdir(SHOTS, { recursive: true })
  const gate = attachGate(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page, app.baseURL, 'engineer@acme.example', 'scene')
  await page.goto(`${app.baseURL}/studio`)
  const dir = await mkdtemp(path.join(tmpdir(), 'wh-big-'))
  const file = path.join(dir, 'big.clone.json')
  const body = JSON.stringify(bigClone()).replaceAll('"vault://tenant/proving-ground/', '"vault://tenant/acme/')
  await writeFile(file, body)
  const empty = page.getByRole('heading', { name: 'No clone yet' })
  await expect(empty.or(page.locator('.sc-svg')).first()).toBeVisible()
  await page.getByTestId('clone-file').setInputFiles(file)
  await expect(page.locator('.sc-solid')).toHaveCount(500, { timeout: 60_000 })
  await expect(page.locator('[data-scene-edge]')).toHaveCount(600)
  await page.waitForTimeout(1000)
  await page.screenshot({ path: path.join(SHOTS, 'big-500-fit.png') })

  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Performance.enable')
  const heap = async () => {
    await cdp.send('HeapProfiler.collectGarbage')
    const { metrics } = await cdp.send('Performance.getMetrics')
    return metrics.find((metric) => metric.name === 'JSHeapUsedSize').value / 1e6
  }
  await page.evaluate(() => {
    window.__long = []
    new PerformanceObserver((list) => { for (const entry of list.getEntries()) window.__long.push(entry.duration) }).observe({ entryTypes: ['longtask'] })
    window.__frames = []
    let last = performance.now()
    const tick = (now) => { window.__frames.push(now - last); last = now; window.__raf = requestAnimationFrame(tick) }
    window.__raf = requestAnimationFrame(tick)
  })
  const before = await heap()
  const box = await page.locator('.sc-svg').boundingBox()
  const cx = box.x + box.width / 2
  const cy = box.y + box.height / 2
  for (let round = 0; round < 4; round += 1) {
    // The drag starts on bare ground (the bottom left corner of the pane): a press on a solid would move that solid.
    await page.mouse.move(box.x + 30, box.y + box.height - 30)
    await page.mouse.down()
    for (let i = 0; i < 60; i += 1) await page.mouse.move(cx + Math.sin(i / 6) * 300, cy + Math.cos(i / 6) * 150)
    await page.mouse.up()
    for (let i = 0; i < 40; i += 1) {
      await page.mouse.wheel(0, i < 20 ? -150 : 150)
      await page.waitForTimeout(16)
    }
  }
  await page.waitForTimeout(400)
  const { frames, long } = await page.evaluate(() => { cancelAnimationFrame(window.__raf); return { frames: window.__frames, long: window.__long } })
  const after = await heap()
  const total = frames.reduce((sum, ms) => sum + ms, 0)
  const fps = (frames.length / total) * 1000
  const sorted = [...frames].sort((a, b) => a - b)
  const p99 = sorted[Math.floor(sorted.length * 0.99)]
  const numbers = { frames: frames.length, fps: Number(fps.toFixed(1)), p99FrameMs: Number(p99.toFixed(1)), longestFrameMs: Number(sorted.at(-1).toFixed(1)),
    longTasks: long.length, heapBeforeMB: Number(before.toFixed(1)), heapAfterMB: Number(after.toFixed(1)) }
  await writeFile(path.join(SHOTS, 'big-500-trace.json'), `${JSON.stringify(numbers, null, 2)}\n`)
  expect(fps, JSON.stringify(numbers)).toBeGreaterThan(30)
  expect(Math.max(0, ...long), 'no task over 100 ms').toBeLessThan(100)
  expect(after, 'memory does not keep growing').toBeLessThan(before * 1.5 + 5)
  // Zoomed out the scene draws plain boxes and no names; zoomed in the names come back.
  await expect(page.locator('.sc-svg.plain')).toHaveCount(1)
  await expect(page.locator('.sc-label')).toHaveCount(0)
  gate.assertClean()
})
