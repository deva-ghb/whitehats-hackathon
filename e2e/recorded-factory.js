/** A stand-in Fix Factory engine that plays back one real recorded run over the engine's own HTTP API. */

import { randomBytes } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const RECORDINGS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../backend/tests/recordings/fix_factory_runs.json')

/** The merged recording as it looked before a person approved it in Fix Factory. */
function awaitingApproval(merged) {
  const early = structuredClone(merged)
  Object.assign(early.row, { approval: 'pending', approved_at: null, merged: null })
  early.events = early.events.filter((e) => e.stage !== 'approve')
  return early
}

export async function startRecordedFactory() {
  const { merged } = JSON.parse(await readFile(RECORDINGS, 'utf8'))
  const token = randomBytes(16).toString('hex')
  const runId = merged.row.id
  let run = null // no run until WhiteHats sends the fix

  const board = () => ({
    summary: { runs: run ? 1 : 0, verified: run ? 1 : 0, approved: run?.row.approval === 'approved' ? 1 : 0,
      avg_seconds: run ? run.row.seconds : null, cost_per_fix: run ? run.row.cost_usd : null },
    runs: run ? [run.row] : [],
  })
  const inbox = () => [{
    ...merged.item, evidence: [], latest_run: run?.row ?? null, can_replay: false,
  }]
  const answer = (method, url) => {
    if (method === 'GET' && url === '/api/runs') return [200, board()]
    if (method === 'GET' && url === '/api/inbox') return [200, inbox()]
    if (method === 'POST' && url === '/api/runs') {
      run = awaitingApproval(merged)
      return [202, { run: runId }]
    }
    if (method === 'GET' && url === `/api/runs/${runId}` && run) return [200, run]
    if (method === 'POST' && url === `/api/runs/${runId}/approve` && run?.row.approval === 'pending') {
      run = merged
      return [200, { message: merged.events.at(-1).message }]
    }
    return [404, { error: 'not found' }]
  }

  const server = http.createServer((req, res) => {
    req.resume()
    req.on('end', () => {
      const [status, body] = req.headers.authorization === `Bearer ${token}`
        ? answer(req.method, req.url)
        : [401, { error: 'bad token' }]
      res.writeHead(status, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify(body))
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    env: { FACTORY_URL: `http://127.0.0.1:${server.address().port}`, WHITEHATS_DISPATCH_TOKEN: token },
    stop: () => new Promise((resolve) => server.close(resolve)),
  }
}
