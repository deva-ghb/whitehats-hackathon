// The clone open on the canvas. Edits stay local, each one is saved as the next version, and each can be undone.
// Undo and redo never rewrite history: they save the earlier (or later) drawing as one more version, with a reason that says so.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useToast } from '../components/ui'

const HISTORY_LIMIT = 100

export function useDocumentEdits({ server, current, author, enabled }) {
  const toast = useToast()
  const { draft, draftRef, replace } = useDraft(server, current, enabled)
  const past = useRef([])
  const future = useRef([])
  const [depth, setDepth] = useState({ undo: 0, redo: 0 })
  const forget = useCallback(() => {
    past.current = []
    future.current = []
    setDepth({ undo: 0, redo: 0 })
  }, [])
  const { save, inflight, failed, whenSaved, adoptSaved } = useSaver({ current, author, replace, draftRef, toast, forget, server })
  const [holding, setHolding] = useState(false)
  const pending = useRef(null)

  const settle = useCallback(() => {
    const waiting = pending.current
    if (!waiting) return
    clearTimeout(waiting.timer)
    pending.current = null
    setHolding(false)
    save(draftRef.current, waiting.reason)
  }, [draftRef, save])

  /**
   * Change the document and save it as a version. Edits that share a `key` and arrive within `wait` ms
   * (typing in one field) are one step in the history and one saved version.
   */
  const apply = useCallback((mutator, reason, { wait = 0, key = null } = {}) => {
    if (!enabled || !draftRef.current) return false
    const before = draftRef.current
    const next = mutator(before)
    if (!next || next === before) return false
    const sameRun = Boolean(pending.current && key && pending.current.key === key)
    if (pending.current && !sameRun) settle()
    if (!sameRun) {
      past.current = [...past.current, { doc: before, reason }].slice(-HISTORY_LIMIT)
      future.current = []
      setDepth({ undo: past.current.length, redo: 0 })
    }
    replace(next)
    if (!wait) {
      save(next, reason)
      return true
    }
    if (pending.current) clearTimeout(pending.current.timer)
    pending.current = { key, reason, timer: setTimeout(settle, wait) }
    setHolding(true)
    return true
  }, [draftRef, enabled, replace, save, settle])

  const step = useCallback((from, to, verb) => {
    if (!enabled || !from.current.length) return false
    settle()
    const entry = from.current.pop()
    to.current = [...to.current, { doc: draftRef.current, reason: entry.reason }].slice(-HISTORY_LIMIT)
    replace(entry.doc)
    save(entry.doc, `${verb}: ${entry.reason}`)
    setDepth({ undo: past.current.length, redo: future.current.length })
    return true
  }, [draftRef, enabled, replace, save, settle])
  // For what the server changes itself (a connection's findings brought in): wait until every edit is saved, then take its version.
  const flush = useCallback(() => { settle(); return whenSaved() }, [settle, whenSaved])
  const adopt = useCallback((document) => { forget(); adoptSaved(document) }, [adoptSaved, forget])
  const undo = useCallback(() => step(past, future, 'Undid'), [step])
  const redo = useCallback(() => step(future, past, 'Redid'), [step])

  useEffect(() => () => {
    if (pending.current) clearTimeout(pending.current.timer)
    pending.current = null
    forget()
  }, [current, forget])
  const saveState = holding || inflight ? 'saving' : failed ? 'error' : 'saved'
  const lastStep = useCallback(() => past.current.at(-1) || null, [])
  return { draft, draftRef, apply, undo, redo, lastStep, flush, adopt, canUndo: depth.undo > 0, canRedo: depth.redo > 0, saveState }
}

function useDraft(server, current, editing) {
  const [draft, setDraft] = useState(null)
  const draftRef = useRef(null)
  const loadedFor = useRef(null)
  const replace = useCallback((next) => {
    draftRef.current = next
    setDraft(next)
  }, [])
  useEffect(() => {
    loadedFor.current = null
    draftRef.current = null
    setDraft(null)
  }, [current])
  useEffect(() => {
    if (!server || String(server.id) !== String(current)) return
    // Drawn by hand, the drawing on screen is the person's own until they save; read only (the connections draw the
    // map), it follows each newer version the server sends, so a connection just placed shows at once.
    const version = `${current}@${server.document?.version?.number}`
    if (loadedFor.current === (editing ? String(current) : version)) return
    loadedFor.current = editing ? String(current) : version
    replace(server.document)
  }, [server, current, replace, editing])
  return { draft, draftRef, replace }
}

// A save the server refuses (an edge the library forbids, a Generic type) puts the drawing back to the last version
// it accepted. A save that could not reach the server keeps the drawing: the next one that does carries it.
function useSaver({ current, author, replace, draftRef, toast, forget, server }) {
  const [inflight, setInflight] = useState(0)
  const [failed, setFailed] = useState(false)
  const queue = useRef(Promise.resolve())
  const idRef = useRef(current)
  const accepted = useRef(null)
  const epoch = useRef(0)
  useEffect(() => { idRef.current = current; accepted.current = null; setFailed(false) }, [current])
  useEffect(() => {
    if (server && String(server.id) === String(current) && !accepted.current) accepted.current = server.document
  }, [server, current])
  const save = useCallback((snapshot, reason) => {
    const id = idRef.current
    if (!id || !snapshot) return
    const started = epoch.current
    setInflight((n) => n + 1)
    const job = queue.current.catch(() => {}).then(async () => {
      if (started !== epoch.current) return
      const out = await api(`/api/clones/${id}/versions`, { method: 'POST', body: { reason, document: snapshot } })
      setFailed(false)
      const stamp = (doc) => ({ ...doc, version: { ...doc.version, number: out.version, author } })
      accepted.current = stamp(snapshot)
      if (String(idRef.current) === String(id) && draftRef.current) replace(stamp(draftRef.current))
    })
    queue.current = job.then(() => {}, () => {})
    job.catch((error) => {
      setFailed(true)
      if (error.status >= 400 && error.status < 500 && accepted.current && String(idRef.current) === String(id)) {
        epoch.current += 1
        replace(accepted.current)
        forget()
        setFailed(false)
        toast(`${error.message || 'That change was refused.'} The clone is back at the last saved version.`, 'error')
        return
      }
      toast(error.message || 'The change was not saved.', 'error')
    }).finally(() => setInflight((n) => Math.max(0, n - 1)))
  }, [author, draftRef, forget, replace, toast])
  const whenSaved = useCallback(() => queue.current, [])
  const adoptSaved = useCallback((document) => { accepted.current = document; replace(document) }, [replace])
  return { save, inflight, failed, whenSaved, adoptSaved }
}
