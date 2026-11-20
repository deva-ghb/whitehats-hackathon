// The panels and buttons that float over the scene (where to watch, search, the view switch, zoom, the key), measured from the
// page in the scene's own pixels. Names keep off them, and opening or fitting the scene keeps what matters out from under them.

const FLOATING = '.sense-panel, .studio-search-soon, .studio-view, .scene-controls, .scene-key'
const MARGIN = 28  // clear space between the drawing and the panels, or the pane's edge where there is none

/** Each floating panel over the scene, as { x, y, width, height } relative to the scene's pane. */
export function reservedRects(root) {
  if (!root?.getBoundingClientRect) return []
  const pane = root.getBoundingClientRect()
  const scope = root.parentElement || document
  const out = []
  for (const element of scope.querySelectorAll(FLOATING)) {
    const r = element.getBoundingClientRect()
    if (!r.width || !r.height) continue
    if (r.right <= pane.left || r.left >= pane.right || r.bottom <= pane.top || r.top >= pane.bottom) continue
    out.push({ x: r.left - pane.left, y: r.top - pane.top, width: r.width, height: r.height })
  }
  return out
}

/**
 * How much of each edge of the pane the panels take, so a fitted scene sits in the open middle: a panel in the top third
 * pushes the top edge down past it, one in the bottom third pushes the bottom edge up. Side panels are small and in the
 * corners, so they count toward top or bottom, never left or right.
 */
export function insetsFor(rects, size) {
  let top = 0
  let bottom = 0
  for (const r of rects) {
    if (r.y + r.height / 2 < size.height / 3) top = Math.max(top, r.y + r.height)
    else if (r.y + r.height / 2 > (size.height * 2) / 3) bottom = Math.max(bottom, size.height - r.y)
  }
  // a tall open key reaches far up; it is in a corner, so the scene may sit beside it
  bottom = Math.min(bottom, size.height * 0.18)
  return { top: top + MARGIN, bottom: bottom + MARGIN, left: MARGIN, right: MARGIN }
}
