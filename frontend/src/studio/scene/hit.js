// Hit testing for the scene: which thing is under a point. Pure functions on scene-space polygons, so they test without a browser.
//
// Every drawn thing carries an `outline`: a convex polygon in scene space that covers what is drawn. A pointer (already moved
// into scene space) hits the topmost thing whose outline holds it. Things are given in paint order, back to front.

/** Convex hull of points (Andrew's monotone chain), counter-clockwise on screen. Fewer than three points come back as given. */
export function hull(points) {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1])
  if (sorted.length < 3) return sorted
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
  const build = (list) => {
    const out = []
    for (const p of list) {
      while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop()
      out.push(p)
    }
    out.pop()
    return out
  }
  return [...build(sorted), ...build([...sorted].reverse())]
}

/** True when the point is inside a convex polygon (or on its edge). */
export function insideConvex(polygon, x, y) {
  if (polygon.length < 3) return false
  let sign = 0
  for (let i = 0; i < polygon.length; i += 1) {
    const [ax, ay] = polygon[i]
    const [bx, by] = polygon[(i + 1) % polygon.length]
    const side = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
    if (side === 0) continue
    const now = side > 0 ? 1 : -1
    if (sign && now !== sign) return false
    sign = now
  }
  return true
}

/** The rectangle that holds a polygon. */
export function boundsOfPolygon(polygon) {
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const [x, y] of polygon) {
    x0 = Math.min(x0, x)
    y0 = Math.min(y0, y)
    x1 = Math.max(x1, x)
    y1 = Math.max(y1, y)
  }
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
}

/** The id of the topmost thing under (x, y), or null. `things` are in paint order, so the last one that holds the point wins. */
export function pickAt(things, x, y) {
  for (let i = things.length - 1; i >= 0; i -= 1) {
    const thing = things[i]
    const box = thing.bounds
    if (x < box.x || y < box.y || x > box.x + box.width || y > box.y + box.height) continue
    if (insideConvex(thing.outline, x, y)) return thing.id
  }
  return null
}

/** Every id whose outline touches a rectangle (a marquee). */
export function pickWithin(things, rect) {
  const found = []
  for (const thing of things) {
    const box = thing.bounds
    if (box.x > rect.x + rect.width || box.x + box.width < rect.x || box.y > rect.y + rect.height || box.y + box.height < rect.y) continue
    found.push(thing.id)
  }
  return found
}

/** Distance from a point to a segment. */
export function distanceToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax
  const dy = by - ay
  const length = dx * dx + dy * dy
  const t = length === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length))
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

/** The id of the nearest edge (a list of {id, points: [[x, y], ...]}) within `reach`, or null. */
export function pickEdge(edges, x, y, reach) {
  let best = null
  let bestDistance = reach
  for (const edge of edges) {
    for (let i = 1; i < edge.points.length; i += 1) {
      const [ax, ay] = edge.points[i - 1]
      const [bx, by] = edge.points[i]
      const distance = distanceToSegment(x, y, ax, ay, bx, by)
      if (distance <= bestDistance) {
        best = edge.id
        bestDistance = distance
      }
    }
  }
  return best
}

/** True when two rectangles overlap, with `gap` of air around each. */
export function rectsOverlap(a, b, gap = 0) {
  return a.x < b.x + b.width + gap && b.x < a.x + a.width + gap && a.y < b.y + b.height + gap && b.y < a.y + a.height + gap
}
