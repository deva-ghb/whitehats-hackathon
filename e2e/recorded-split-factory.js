/** A stand-in Fix Factory engine that plays back the recorded pull request line over the engine's own HTTP API. */

import { randomBytes } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const RECORDINGS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../backend/tests/recordings/fix_factory_split_runs.json')

/** The recorded run as it looked while it was still working: only its first line, no pieces yet. */
function working(detail) {
  return {
    ...detail,
    row: { ...detail.row, status: 'running', approval: null, finished_at: null, score: null, delivered: null },
    events: detail.events.slice(0, 1),
    pieces: [],
    verification: [],
  }
}

export async function startRecordedSplitFactory() {
  const recorded = JSON.parse(await readFile(RECORDINGS, 'utf8'))
  const token = randomBytes(16).toString('hex')
  let next = 'opened' // the recording the next Apply Fix plays back: opened or ci_failed
  let run = null

  const item = () => ({
    ...recorded.opened.item, evidence: [], delivers: 'pull_request', has_proof: true, latest_run: run?.row ?? null, can_replay: false,
  })
  const board = () => ({
    summary: { runs: run ? 1 : 0, verified: run?.row.status === 'verified' ? 1 : 0, approved: run?.row.approval === 'approved' ? 1 : 0,
      avg_seconds: run?.row.finished_at ? run.row.seconds : null, cost_per_fix: run?.row.finished_at ? run.row.cost_usd : null },
    runs: run ? [run.row] : [],
  })
  const answer = (method, url) => {
    if (method === 'GET' && url === '/api/runs') return [200, board()]
    if (method === 'GET' && url === '/api/inbox') return [200, [item()]]
    if (method === 'POST' && url === '/api/runs') {
      run = working(recorded[next])
      return [202, { run: run.row.id }]
    }
    if (method === 'GET' && run && url === `/api/runs/${run.row.id}`) return [200, run]
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
    /** Which recording the next Apply Fix plays back. */
    playNext: (name) => { next = name; run = null },
    /** Move the current run on to a later recorded state of the same run (opened, then merged). */
    advance: (name) => { run = recorded[name] },
    stop: () => new Promise((resolve) => server.close(resolve)),
  }
}
