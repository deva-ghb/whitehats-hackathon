// The view of the scene: where it is panned and how far it is zoomed, the size of the pane, and the way a pointer on the
// pane becomes a ground point. Panning and zooming write the transform straight to the page, so nothing is redrawn;
// `settled` follows a moment later and is what level of detail and name placement are worked out from.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { MIN_ZOOM, fitView, screenToGround, zoomAbout } from './project'
import { insetsFor, reservedRects } from './reserved'

const SETTLE_MS = 100
const OPEN_MIN = 0.2          // a small clone opens whole, unless that would be smaller than this
const OPEN_MAX = 2.2          // and a tiny one is not blown up past this
export const SMALL_CLONE = 80 // up to this many nodes a clone opens at a readable size, not necessarily whole

export function useSceneView({ sceneRef }) {
  const root = useRef(null)
  const svg = useRef(null)
  const world = useRef(null)
  const viewRef = useRef({ x: 0, y: 0, zoom: 1 })
  const settleTimer = useRef(0)
  const touched = useRef(false)      // the person has moved the view: it is theirs now, and a resize no longer refits it
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [settled, setSettled] = useState({ x: 0, y: 0, zoom: 1 })
  const sizeRef = useRef(size)
  sizeRef.current = size

  const settle = useCallback(() => {
    window.clearTimeout(settleTimer.current)
    settleTimer.current = window.setTimeout(() => setSettled({ ...viewRef.current }), SETTLE_MS)
  }, [])
  const applyView = useCallback((view) => {
    viewRef.current = view
    world.current?.setAttribute('transform', `translate(${view.x.toFixed(2)} ${view.y.toFixed(2)}) scale(${view.zoom.toFixed(4)})`)
    root.current?.style.setProperty('--sc-z', view.zoom.toFixed(4))
    settle()
  }, [settle])
  useEffect(() => () => window.clearTimeout(settleTimer.current), [])
  useLayoutEffect(() => { applyView(viewRef.current) }, [applyView])

  useEffect(() => {
    const element = root.current
    if (!element) return undefined
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight })
    measure()
    const watcher = new ResizeObserver(measure)
    watcher.observe(element)
    return () => watcher.disconnect()
  }, [])

  const fit = useCallback(() => {
    const { bounds, solids, floats } = sceneRef.current
    const canvas = sizeRef.current
    if (!bounds || !canvas.width) return
    const nodes = solids.length + floats.length
    const insets = insetsFor(reservedRects(root.current), canvas)
    applyView(fitView(bounds, canvas, { insets, maxZoom: OPEN_MAX, minZoom: nodes <= SMALL_CLONE ? OPEN_MIN : MIN_ZOOM }))
  }, [applyView, sceneRef])

  const local = useCallback((clientX, clientY) => {
    const rect = root.current.getBoundingClientRect()
    return [clientX - rect.left, clientY - rect.top]
  }, [])
  const pointAt = useCallback((clientX, clientY) => {
    const [px, py] = local(clientX, clientY)
    return screenToGround(viewRef.current, px, py)
  }, [local])
  const zoomBy = useCallback((factor) => {
    touched.current = true
    const canvas = sizeRef.current
    applyView(zoomAbout(viewRef.current, canvas.width / 2, canvas.height / 2, viewRef.current.zoom * factor))
  }, [applyView])
  const panBy = useCallback((dx, dy) => {
    touched.current = true
    applyView({ ...viewRef.current, x: viewRef.current.x + dx, y: viewRef.current.y + dy })
  }, [applyView])

  return { root, svg, world, viewRef, sizeRef, size, settled, touched, settle, applyView, fit, local, pointAt, zoomBy, panBy }
}

/**
 * Opening a clone shows all of it, once the clone itself has arrived (for a moment after switching, the old drawing is still
 * held). A person's own edit never moves the view. While the person has not moved the view, it refits when the pane is resized
 * and when the drawing grows or shrinks (a connection bringing in what it found), so the whole map stays in sight.
 */
export function useOpeningFit({ view, current, cloneDoc, hasBounds, boundsKey = '', empty, followGrowth = false }) {
  const { size, touched, fit } = view
  const fitted = useRef({ current: null, size: '', bounds: '' })
  // Whatever is held when this surface first draws belongs to the clone it was opened on (switching from the plan view mounts it
  // with the clone already loaded); only a later change of clone leaves the old drawing in place for a moment.
  const stale = useRef(null)
  if (stale.current === null) stale.current = { current, doc: null }
  else if (stale.current.current !== current) stale.current = { current, doc: cloneDoc }
  useEffect(() => {
    if (!cloneDoc || cloneDoc === stale.current.doc || !size.width || (!hasBounds && !empty)) return
    const where = `${size.width}x${size.height}`
    if (fitted.current.current !== current) {
      fitted.current = { current, size: where, bounds: boundsKey }
      touched.current = false
      if (hasBounds) fit()
    } else if (!touched.current && hasBounds && (fitted.current.size !== where || (followGrowth && fitted.current.bounds !== boundsKey))) {
      fitted.current.size = where
      fitted.current.bounds = boundsKey
      fit()
    }
  }, [boundsKey, cloneDoc, current, empty, fit, followGrowth, hasBounds, size.width, size.height, touched])
}
