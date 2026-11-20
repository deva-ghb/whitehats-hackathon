// The sense points stored for the open clone (P6.3): read them, run the analysis, accept, reject, place by hand, remove.
// The server keeps them per project and clone version and works out the coverage; this hook only holds the latest answer.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'

const NONE = { data: null, error: null, busy: null }
const NO_POINTS = []   // one array for "none yet", so a screen that depends on the list is not redrawn by a new empty one each time

/**
 * `cloneId` is the saved clone (null when there is none to ask about, as for what the connections found). Whether the points are
 * from an older drawing is the panel's to say: it knows the version on screen, and editing never reads this again.
 */
export function useSensePoints(cloneId) {
  const [state, setState] = useState(NONE)
  const seq = useRef(0)
  const base = cloneId ? `/api/clones/${cloneId}/sense-points` : null

  const read = useCallback(async () => {
    if (!base) return
    const mine = ++seq.current
    try {
      const data = await api(base)
      if (mine === seq.current) setState((now) => ({ ...now, data, error: null }))
    } catch (error) {
      if (mine === seq.current) setState((now) => ({ ...now, error: error.message }))
    }
  }, [base])
  useEffect(() => {
    setState(NONE)
    read()
  }, [read])

  // Every change answers with the whole picture, so what is shown is what the server now holds.
  const change = useCallback(async (key, request) => {
    setState((now) => ({ ...now, busy: key, error: null }))
    try {
      const data = await request()
      seq.current += 1
      setState({ data, error: null, busy: null })
      return data
    } catch (error) {
      setState((now) => ({ ...now, busy: null, error: error.message }))
      return null
    }
  }, [])

  const analyse = useCallback(() => change('analyse', () => api(`${base}/analyse`, { method: 'POST' })), [base, change])
  const decide = useCallback((id, status) => change(`point-${id}`, () => api(`/api/sense-points/${id}`, { method: 'PATCH', body: { status } })), [change])
  const place = useCallback((node) => change(`place-${node}`, () => api(base, { method: 'POST', body: { node } })), [base, change])
  const remove = useCallback((id) => change(`point-${id}`, () => api(`/api/sense-points/${id}`, { method: 'DELETE' })), [change])

  return { ...state, points: state.data?.points || NO_POINTS, analyse, decide, place, remove, reload: read }
}
