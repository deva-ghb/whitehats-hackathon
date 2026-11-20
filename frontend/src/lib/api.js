import { useCallback, useEffect, useRef, useState } from 'react'

const TOKEN_KEY = 'wh.token'
const OPERATOR_KEY = 'wh.token.operator'

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(t) {
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* private mode */ }
}

// A support session swaps in a short read-only token and keeps the operator's own token aside.
export function startSupport(token) {
  try { localStorage.setItem(OPERATOR_KEY, getToken() || ''); localStorage.setItem(TOKEN_KEY, token) } catch { /* ignore */ }
}
export function endSupport() {
  try {
    const op = localStorage.getItem(OPERATOR_KEY)
    localStorage.removeItem(OPERATOR_KEY)
    op ? localStorage.setItem(TOKEN_KEY, op) : localStorage.removeItem(TOKEN_KEY)
  } catch { /* ignore */ }
}
export function inSupport() {
  try { return !!localStorage.getItem(OPERATOR_KEY) } catch { return false }
}

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status }
}

let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) { onUnauthorized = fn }

// The screen sets this from the URL (/p/<slug>/...). Requests then name that project.
let projectSlug = ''
export function setProjectSlug(slug) { projectSlug = slug || '' }
export function getProjectSlug() { return projectSlug }

function withProject(path) {
  if (!projectSlug || !path.startsWith('/api/') || path.includes('project=')) return path
  if (path.startsWith('/api/auth/login') || path.startsWith('/api/auth/mfa')) return path
  const join = path.includes('?') ? '&' : '?'
  return `${path}${join}project=${encodeURIComponent(projectSlug)}`
}

const inflight = new Map()

export async function api(path, { method = 'GET', body } = {}) {
  path = withProject(path)
  if (method === 'GET' && body === undefined) {
    const pending = inflight.get(path)
    if (pending) return pending
    const request = requestOnce(path, method, body).finally(() => inflight.delete(path))
    inflight.set(path, request)
    return request
  }
  return requestOnce(path, method, body)
}

async function requestOnce(path, method, body) {
  const headers = { Accept: 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined })
  } catch {
    throw new ApiError('Cannot reach the WhiteHats server. Check that it is running.', 0)
  }
  let data = null
  try { data = await res.json() } catch { /* empty body */ }
  if (res.status === 401 && path !== '/api/auth/login') onUnauthorized()
  if (!res.ok) {
    const detail = data?.detail
    const msg = Array.isArray(detail) ? detail.map((d) => (typeof d === 'string' ? d : `${d.loc?.slice(-1)[0]}: ${d.msg}`)).join('; ') : detail
    throw new ApiError(msg || `Request failed (${res.status})`, res.status)
  }
  return data
}

// Load data for a screen; optional polling. Returns { data, error, loading, reload, setData }.
export function useApi(path, { poll = 0, deps = [] } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const seq = useRef(0)
  const scoped = getProjectSlug()
  const reload = useCallback(async () => {
    if (!path) return
    const mine = ++seq.current
    try {
      const d = await api(path)
      if (mine === seq.current) { setData(d); setError(null) }
    } catch (e) {
      if (mine === seq.current) setError(e)
    } finally {
      if (mine === seq.current) setLoading(false)
    }
  }, [path, scoped])
  useEffect(() => {
    // Drop the previous payload when the project changes, so one project's rows never sit on another's screen.
    setData(null)
    setError(null)
    setLoading(true)
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, ...deps])
  // A separate effect, so a screen can slow its polling without dropping what it shows.
  useEffect(() => {
    if (!poll) return undefined
    const id = setInterval(reload, poll)
    return () => clearInterval(id)
  }, [reload, poll])
  return { data, error, loading, reload, setData }
}

export const fmt = {
  n: (v) => (v == null ? '—' : Number(v).toLocaleString()),
  pct: (v) => (v == null ? '—' : `${v}%`),
  ago(iso) {
    if (!iso) return 'never'
    const s = (Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime()) / 1000
    if (s < 45) return 'just now'
    if (s < 3600) return `${Math.round(s / 60)} min ago`
    if (s < 86400) return `${Math.round(s / 3600)} h ago`
    return `${Math.round(s / 86400)} d ago`
  },
  date(iso) {
    if (!iso) return '—'
    const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso + (iso.endsWith('Z') ? '' : 'Z'))
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  },
  time(iso) {
    if (!iso) return '—'
    return new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  },
  tokens(v) {
    if (v == null) return '—'
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
    if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`
    return String(v)
  },
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true } catch { return false }
}
