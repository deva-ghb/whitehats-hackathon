/**
 * A fake GitHub the app is pointed at (GITHUB_API_URL). It answers from a recording of the real proving-ground repository,
 * made through the WhiteHats App, so the app reads real files and never reaches github.com. The recording was made with small
 * pages; a list is put back together and cut again to the page size the app asks for. Every request is kept, so a test can say
 * the app only read.
 */

import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { generateKeyPairSync } from 'node:crypto'
import http from 'node:http'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const RECORDING = path.resolve(HERE, '../backend/tests/recordings/connectors/github/scan-ok.json')
export const INSTALLATION = '12345678'  // the placeholder id the recording was made with
const REAL = 'https://api.github.com'

const RECORDED_REPOSITORY = 'whitehats-proving-ground'

/** More repositories for the installation: each is the recorded repository under another name, with every answer that names it. */
function addRepositories(recording, names) {
  const recorded = recording.calls.filter((call) => call.url.includes(`/${RECORDED_REPOSITORY}`))
  for (const name of names) {
    recording.calls.push(...recorded.map((call) => JSON.parse(JSON.stringify(call).replaceAll(RECORDED_REPOSITORY, name))))
    for (const call of recording.calls) {
      const repositories = call.body?.repositories
      const original = repositories?.find((repository) => repository.name === RECORDED_REPOSITORY)
      if (!original || repositories.some((repository) => repository.name === name)) continue
      repositories.push(JSON.parse(JSON.stringify(original).replaceAll(RECORDED_REPOSITORY, name)))
      call.body.total_count += 1
    }
  }
}

export async function startFakeGithub({ extraRepositories = [] } = {}) {
  const recording = JSON.parse(await readFile(RECORDING, 'utf8'))
  addRepositories(recording, extraRepositories)
  const answers = new Map(recording.calls.map((call) => [asked(call.method, call.url), call]))
  const lists = listings(recording.calls)
  const requests = []
  let signIn = null  // an answer to give the App's sign-in instead of the recorded one
  const server = http.createServer((request, response) => {
    requests.push(`${request.method} ${request.url}`)
    request.resume()
    const key = asked(request.method, `${REAL}${request.url}`)
    // The App's check of the pull request permission lists open pull requests; the recording has none.
    if (request.method === 'GET' && new URL(`${REAL}${request.url}`).pathname.endsWith('/pulls')) return send(response, 200, {}, [])
    const listed = request.method === 'GET' ? page(lists, new URL(`${REAL}${request.url}`), server) : null
    if (listed) return send(response, 200, listed.headers, listed.body)
    const call = request.method === 'POST' && signIn ? signIn : answers.get(key)
    if (!call) return send(response, 404, {}, { message: 'Not Found (the fake GitHub holds no such recording)' })
    const body = request.method === 'POST' && !signIn
      ? { ...call.body, expires_at: new Date(Date.now() + 3_600_000).toISOString().replace(/\.\d+Z$/, 'Z') }
      : call.body
    return send(response, call.status, rewrite(call.headers || {}, server), body)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${server.address().port}`
  const keyFile = path.join(await mkdtemp(path.join(tmpdir(), 'wh-fake-github-')), 'app.pem')
  const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
  await writeFile(keyFile, privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 })
  return {
    url,
    requests,
    // What the app needs to reach this fake instead of GitHub. The key is made for this run and signs for nothing real.
    env: { GITHUB_API_URL: url, GITHUB_APP_ID: '1', GITHUB_APP_PRIVATE_KEY_PATH: keyFile },
    /** Make the App's sign-in fail the way GitHub answers a key it does not know. */
    refuseSignIn: () => { signIn = { status: 401, body: { message: 'Bad credentials' } } },
    allowSignIn: () => { signIn = null },
    stop: () => new Promise((resolve) => server.close(resolve)),
  }
}

// The same request with its query in a fixed order, so the order the app writes it in does not matter.
function asked(method, address) {
  const parsed = new URL(address)
  parsed.searchParams.sort()
  return `${method} ${parsed.pathname}${parsed.search}`
}

function rewrite(headers, server) {
  const base = `http://127.0.0.1:${server.address().port}`
  return Object.fromEntries(Object.entries(headers).map(([name, value]) => [name, String(value).replaceAll(REAL, base)]))
}

function send(response, status, headers, body) {
  response.writeHead(status, { 'content-type': 'application/json', ...headers })
  response.end(JSON.stringify(body))
}

// Lists the recording holds page by page, put back together: path (as /repos/owner/name/...) -> { items, key } where key is the
// field of the answer that holds the items, or null when the answer is the list itself.
function listings(calls) {
  const names = new Map()  // repository id -> owner/name, because GitHub's next-page links use /repositories/<id>
  for (const call of calls) {
    for (const repo of (call.body?.repositories || [])) names.set(String(repo.id), repo.full_name)
  }
  const found = new Map()
  for (const call of calls) {
    const url = new URL(call.url)
    if (call.method !== 'GET' || !url.searchParams.has('per_page')) continue
    const where = canonical(url.pathname, names)
    const key = Array.isArray(call.body) ? null : Object.keys(call.body).find((name) => Array.isArray(call.body[name]))
    const items = key === null ? call.body : call.body[key]
    const held = found.get(where) || { items: [], key, rest: key === null ? null : call.body, names }
    const first = (Number(url.searchParams.get('page') || 1) - 1) * Number(url.searchParams.get('per_page'))
    held.items.splice(first, items.length, ...items)
    found.set(where, held)
  }
  return found
}

function canonical(pathname, names) {
  const match = pathname.split('/')
  return match[1] === 'repositories' && names.has(match[2]) ? `/repos/${names.get(match[2])}/${match.slice(3).join('/')}` : pathname
}

function page(lists, url, server) {
  const names = lists.values().next().value?.names || new Map()
  const held = lists.get(canonical(url.pathname, names))
  if (!held) return null
  const size = Number(url.searchParams.get('per_page') || 30)
  const number = Number(url.searchParams.get('page') || 1)
  const slice = held.items.slice((number - 1) * size, number * size)
  const more = number * size < held.items.length
  const next = new URL(url)
  next.searchParams.set('page', String(number + 1))
  const base = `http://127.0.0.1:${server.address().port}`
  return {
    body: held.key === null ? slice : { ...held.rest, total_count: held.items.length, [held.key]: slice },
    headers: more ? { link: `<${base}${next.pathname}${next.search}>; rel="next"` } : {},
  }
}
