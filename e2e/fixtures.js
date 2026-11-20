/** Starts the platform once per worker, on a database that exists only for that run. */

import { test as base } from '@playwright/test'
import { spawn } from 'node:child_process'
import { access, mkdtemp, rm } from 'node:fs/promises'
import net from 'node:net'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const BACKEND = path.resolve(HERE, '../backend')
const WEB_DIST = path.resolve(HERE, '../frontend/dist/index.html')

export const test = base.extend({
  app: [async ({}, use) => {
    const started = await startApp()
    await use({ baseURL: started.baseURL, dataDir: started.dataDir })
    await started.stop()
  }, { scope: 'worker' }],
})

/** Extra environment (for example a stand-in Fix Factory engine) is added after the isolation rules. */
export async function startApp(extraEnv = {}) {
  await access(WEB_DIST).catch(() => {
    throw new Error('The web app is not built. From platform/frontend run: npm run build')
  })
  const dataDir = await mkdtemp(path.join(tmpdir(), 'wh-e2e-'))
  const port = await freePort()
  const child = spawn(
    'uv',
    ['run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    { cwd: BACKEND, env: { ...serverEnv(dataDir, port), ...extraEnv }, stdio: ['ignore', 'pipe', 'pipe'] },
  )
  let log = ''
  child.stdout.on('data', (chunk) => { log = tail(log, chunk) })
  child.stderr.on('data', (chunk) => { log = tail(log, chunk) })
  try {
    await waitForHealth(port, child, () => log)
  } catch (error) {
    child.kill('SIGTERM')
    await rm(dataDir, { recursive: true, force: true })
    throw error
  }
  return {
    baseURL: `http://127.0.0.1:${port}`,
    dataDir,
    stop: async () => {
      child.kill('SIGTERM')
      await new Promise((resolve) => child.once('exit', resolve))
      await rm(dataDir, { recursive: true, force: true })
    },
  }
}

function serverEnv(dataDir, port) {
  const env = {}
  for (const [key, value] of Object.entries(process.env)) {
    if (key.startsWith('AWS_') || key.startsWith('AZURE_') || key.startsWith('WH_')) continue
    if (key.startsWith('GITHUB_') || key === 'GH_TOKEN' || key === 'WHITEHATS_DISPATCH_TOKEN') continue  // the real GitHub App too
    env[key] = value
  }
  env.WH_DATA_DIR = dataDir
  env.WH_PUBLIC_URL = `http://127.0.0.1:${port}`
  env.WH_ADMIN_PASSWORD = 'WhiteHats!2026'
  env.WH_LOAD_DOTENV = '0'
  // The journeys draw clones by hand; the read-only Studio's own journey (STUDIO.7) passes '0' to startApp.
  env.WH_STUDIO_HAND_EDITING = '1'
  return env
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

async function waitForHealth(port, child, log) {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`The platform exited before it was ready.\n${log()}`)
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`)
      if (response.ok) return
    } catch {
      // The process is still binding the port.
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`The platform did not answer /api/health in 30 seconds.\n${log()}`)
}

function tail(soFar, chunk) {
  return (soFar + chunk.toString()).slice(-4000)
}
