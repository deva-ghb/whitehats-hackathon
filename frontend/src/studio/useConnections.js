// The connections a connection node speaks for: what they are and how they stand, and bringing what one found into the open clone.
import { useCallback, useMemo } from 'react'
import { api, useApi } from '../lib/api'

export function useConnections({ cloneId, edits, enabled, after, landed }) {
  const list = useApi(enabled ? '/api/connectors' : null)
  const byKey = useMemo(() => new Map((list.data || []).map((connector) => [connector.key, connector])), [list.data])

  /** Save every edit, ask the server to bring in what the connection found, then take the version it made. */
  const bringIn = useCallback(async (key) => {
    await edits.flush()
    const done = await api(`/api/clones/${cloneId}/connections/${key}/bring-in`, { method: 'POST' })
    if (done.change_set) {
      const fresh = await api(`/api/clones/${cloneId}`)
      edits.adopt(fresh.document)
      landed?.()  // once the new cards are drawn, so the person sees what arrived
    }
    after?.()
    return done
  }, [cloneId, edits, after, landed])

  // A connection's standing and the marks on what it found change together, so they are read again together.
  const reload = useCallback(() => Promise.all([list.reload(), after?.()]), [list.reload, after])  // eslint-disable-line react-hooks/exhaustive-deps
  return { byKey, loading: list.loading && !list.data, reload, bringIn }
}

/** What a bring-in did, in words. */
export function bringInWords(done) {
  const parts = []
  if (done.added) parts.push(`${done.added} added`)
  if (done.updated) parts.push(`${done.updated} updated`)
  if (done.removed) parts.push(`${done.removed} removed`)
  const said = parts.length ? parts.join(', ') : 'nothing changed'
  return done.held ? `${said}. ${done.held} ${done.held === 1 ? 'needs a type' : 'need a type'} before ${done.held === 1 ? 'it' : 'they'} can go in.` : `${said}.`
}
