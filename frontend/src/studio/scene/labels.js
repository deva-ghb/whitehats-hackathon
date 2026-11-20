// Which names the scene shows, and where each one goes. Names are always 12 px on screen, so zooming out brings them closer
// together: the most important names are placed first, each in the nearest place where it covers no other name, no solid,
// no person badge and no sense point; a name that has to stand a little way off gets a thin leader line back to its solid; and
// a name with no free place is left out (the card on hover and the side panel always have it). None at all below a zoom.
import { rectsOverlap } from './hit.js'
import { sceneToScreen } from './project.js'

export const LABEL_HEIGHT = 20
export const SUB_HEIGHT = 36   // a name with its kind underneath (the selected one, and every name when zoomed in)
const CHAR = 7.0               // average width of a 12 px name, in pixels
const PAD = 10
const AIR = 3                  // clear space kept between a name and anything else
const CELL = 96                // the grid the obstacles are kept in, in pixels
const MOST = 90                // names placed at most, so a crowd never costs a slow frame

const SEPARATORS = new Set(['-', '_', '/', '.', ':', ' '])
const isNumber = (part) => part.length >= 6 && [...part].every((ch) => ch >= '0' && ch <= '9')

/**
 * A name short enough for the map that still tells things apart. Names from cloud accounts tend to share their start and
 * differ at the end (whitehats-proving-ground-509594260364-receipts, -statements), so the end is what is kept: long runs of
 * digits (account numbers) go first, then the name keeps as many whole parts from its end as fit after a "…". The full
 * name is always on the card and in the side panel.
 */
export function shortName(name, most = 26) {
  const text = String(name ?? '')
  if (text.length <= most) return text
  const parts = []  // each part with the separators in front of it
  let current = ''
  for (const ch of text) {
    if (SEPARATORS.has(ch) && current && !SEPARATORS.has(current[current.length - 1])) {
      parts.push(current)
      current = ''
    }
    current += ch
  }
  if (current) parts.push(current)
  const bare = (part) => [...part].filter((ch) => !SEPARATORS.has(ch)).join('')
  const kept = parts.filter((part, i) => !isNumber(bare(part)) || i === 0)
  const whole = kept.join('')
  if (whole.length <= most) return whole
  let tail = ''
  for (let i = kept.length - 1; i > 0; i -= 1) {
    const next = kept[i] + tail
    if (bare(next[0]) === '' ? next.length > most : next.length + 1 > most) break
    tail = next
  }
  const end = [...tail].findIndex((ch) => !SEPARATORS.has(ch))
  return tail ? `…${tail.slice(end)}` : `${whole.slice(0, most - 1)}…`
}

/** The width of a name on screen, in pixels (an estimate, kept on the roomy side). */
export const labelWidth = (text) => Math.ceil(text.length * CHAR) + PAD

/** True when a rectangle and a convex polygon (points [x, y], in the same space) share any ground. Separating axis test. */
export function rectHitsPolygon(rect, polygon, air = 0) {
  if (polygon.length < 3) return false
  const r = { x: rect.x - air, y: rect.y - air, width: rect.width + air * 2, height: rect.height + air * 2 }
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
  if (x1 < r.x || x0 > r.x + r.width || y1 < r.y || y0 > r.y + r.height) return false
  const corners = [[r.x, r.y], [r.x + r.width, r.y], [r.x + r.width, r.y + r.height], [r.x, r.y + r.height]]
  for (let i = 0; i < polygon.length; i += 1) {
    const [ax, ay] = polygon[i]
    const [bx, by] = polygon[(i + 1) % polygon.length]
    const nx = ay - by
    const ny = bx - ax
    let low = Infinity
    let high = -Infinity
    for (const [cx, cy] of corners) {
      const d = cx * nx + cy * ny
      low = Math.min(low, d)
      high = Math.max(high, d)
    }
    let pLow = Infinity
    let pHigh = -Infinity
    for (const [px, py] of polygon) {
      const d = px * nx + py * ny
      pLow = Math.min(pLow, d)
      pHigh = Math.max(pHigh, d)
    }
    if (high < pLow || low > pHigh) return false
  }
  return true
}

/** True when segments ab and cd cross (touching at an end does not count). */
function segmentsCross([ax, ay], [bx, by], [cx, cy], [dx, dy]) {
  const turn = (px, py, qx, qy, rx, ry) => (qx - px) * (ry - py) - (qy - py) * (rx - px)
  const d1 = turn(cx, cy, dx, dy, ax, ay)
  const d2 = turn(cx, cy, dx, dy, bx, by)
  const d3 = turn(ax, ay, bx, by, cx, cy)
  const d4 = turn(ax, ay, bx, by, dx, dy)
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
}

/** True when segment ab passes through rectangle r (shrunk by `inset`, so a line that only grazes an edge does not count). */
function segmentHitsRect(a, b, r, inset = 1) {
  const x0 = r.x + inset
  const y0 = r.y + inset
  const x1 = r.x + r.width - inset
  const y1 = r.y + r.height - inset
  if (x1 <= x0 || y1 <= y0) return false
  const inside = ([x, y]) => x > x0 && x < x1 && y > y0 && y < y1
  if (inside(a) || inside(b)) return true
  const corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
  return corners.some((c, i) => segmentsCross(a, b, c, corners[(i + 1) % 4]))
}

/** A grid of obstacles, so a name is tested against the few things near it and not against the whole scene. */
function indexOf(obstacles) {
  const cells = new Map()
  for (const item of obstacles) {
    const { x, y, width, height } = item.rect
    for (let gx = Math.floor(x / CELL); gx <= Math.floor((x + width) / CELL); gx += 1) {
      for (let gy = Math.floor(y / CELL); gy <= Math.floor((y + height) / CELL); gy += 1) {
        const key = `${gx},${gy}`
        if (!cells.has(key)) cells.set(key, [])
        cells.get(key).push(item)
      }
    }
  }
  return (rect, skip) => {
    const seen = new Set()
    const found = []
    for (let gx = Math.floor((rect.x - AIR) / CELL); gx <= Math.floor((rect.x + rect.width + AIR) / CELL); gx += 1) {
      for (let gy = Math.floor((rect.y - AIR) / CELL); gy <= Math.floor((rect.y + rect.height + AIR) / CELL); gy += 1) {
        for (const item of cells.get(`${gx},${gy}`) || []) {
          if (item.id === skip || seen.has(item)) continue
          seen.add(item)
          found.push(item)
        }
      }
    }
    return found
  }
}

/**
 * The places a name may go, nearest first, as the middle of its rectangle relative to the solid's anchor (below its foot), in
 * screen pixels. Each is { at, cx, cy, rect, leader }: `leader` means the name stands off and gets a line back to the solid.
 */
function spotsFor(candidate, anchor, own, width, height) {
  const w = width
  const h = height
  // Where a leader line runs: from the edge of the solid that faces the name to the edge of the name that faces the solid.
  const joins = (cx, cy, at) => {
    if (!own || at === 'zone') return null
    const top = own.y - anchor[1]
    const middle = own.y + own.height / 2 - anchor[1]
    if (at === 'above') return { from: [0, top], to: [cx, cy + h / 2] }
    if (at === 'right') return { from: [own.x + own.width - anchor[0], middle], to: [cx - w / 2, cy] }
    if (at === 'left') return { from: [own.x - anchor[0], middle], to: [cx + w / 2, cy] }
    return { from: [0, 0], to: [cx, cy - h / 2] }
  }
  const side = (cx, cy, at, leader = false) => ({ at, leader, cx, cy, ...(leader ? joins(cx, cy, at) : {}), rect: { x: cx - w / 2, y: cy - h / 2, width: w, height: h } })
  if (candidate.zone) {
    // At the zone's left corner, and failing that along its back wall, where the wall keeps it clear of what stands inside.
    const base = -4 - LABEL_HEIGHT / 2 + 1
    const spots = [side(0, base, 'zone')]
    const reach = candidate.reach || { left: 0, right: 0 }
    for (const k of [1, 2, 3]) {
      const step = k * (w / 2 + 14)
      if (step + w / 2 <= reach.right) spots.push(side(step, base + step * 0.577, 'zone'))
      if (step + w / 2 <= reach.left) spots.push(side(-step, base + step * 0.577, 'zone'))
    }
    return spots
  }
  const below = 3 + h / 2
  const above = own ? own.y - anchor[1] - 4 - h / 2 : -40
  const left = own ? own.x - anchor[0] - 6 - w / 2 : -w
  const right = own ? own.x + own.width - anchor[0] + 6 + w / 2 : w
  const middle = own ? own.y + own.height / 2 - anchor[1] : -20
  const spots = [side(0, below, 'below'), side(0, above, 'above'), side(right, middle, 'right'), side(left, middle, 'left')]
  if (candidate.must) return spots
  const reach = w / 2 + 12
  const standOff = [[-reach, below + 8, 'below'], [reach, below + 8, 'below'], [-reach, above - 8, 'above'], [reach, above - 8, 'above'],
    [0, below + 20, 'below'], [0, above - 20, 'above'], [0, below + 38, 'below'], [0, above - 38, 'above'],
    [right + 22, middle + 22, 'right'], [left - 22, middle + 22, 'left'], [right + 22, middle - 22, 'right'], [left - 22, middle - 22, 'left']]
  for (const [cx, cy, at] of standOff) spots.push(side(cx, cy, at, true))
  return spots
}

/**
 * candidates: [{ id, anchor, own, text, zone, reach, must, sub }] most important first. `anchor` is the point under the solid's foot and
 * `own` the rectangle of the solid, both in scene space. A zone's name sits above its back corner or, failing that, along its two
 * back walls, as far as `reach` (the length of each wall across the screen, in scene pixels). `must` ones (the selected) are
 * always shown, in the first place, even over others.
 * obstacles: [{ id, kind, rect, polygon? }] in screen pixels: kind 'solid', 'float' (a person badge), 'sense' or 'ui' (a panel or
 * button floating over the scene). A name never covers one (a zone's name may stand over solids, but not over the rest), and a
 * leader line never runs under a panel, across another name, or across another leader line.
 * Returns a Map from each id shown to { at, dx, dy, leader, from, to }: where the middle of its name is, from the anchor, in pixels,
 * and for a name that stands off the two ends of its leader line.
 */
export function placeLabels(view, size, candidates, { min = 0.38, obstacles = [] } = {}) {
  const shown = new Map()
  const taken = []
  const leaders = []  // [from, to] in screen pixels, of every leader line placed so far
  const ui = obstacles.filter((item) => item.kind === 'ui').map((item) => item.rect)
  const near = indexOf(obstacles)
  const onscreen = (rect) => rect.x >= 4 && rect.y >= 4 && rect.x + rect.width <= size.width - 4 && rect.y + rect.height <= size.height - 4
  for (const c of candidates) {
    if (shown.size >= MOST && !c.must) break
    if (view.zoom < min && !c.must) continue
    const [ax, ay] = sceneToScreen(view, c.anchor[0], c.anchor[1])
    let own = null
    if (c.own) {
      const [ox, oy] = sceneToScreen(view, c.own.x, c.own.y)
      own = { x: ox, y: oy, width: c.own.width * view.zoom, height: c.own.height * view.zoom }
    }
    const width = labelWidth(c.text)
    const height = c.sub ? SUB_HEIGHT : LABEL_HEIGHT
    const spots = spotsFor({ ...c, reach: c.reach && { left: c.reach.left * view.zoom, right: c.reach.right * view.zoom } }, [ax, ay], own, width, height)
    const screenRect = (spot) => ({ x: ax + spot.rect.x, y: ay + spot.rect.y, width: spot.rect.width, height: spot.rect.height })
    const leaderOf = (spot) => [[ax + spot.from[0], ay + spot.from[1]], [ax + spot.to[0], ay + spot.to[1]]]
    const free = (spot, overSolids = false) => {
      const rect = screenRect(spot)
      if (!onscreen(rect)) return false
      if (taken.some((other) => rectsOverlap(rect, other, AIR))) return false
      if (leaders.some(([a, b]) => segmentHitsRect(a, b, rect))) return false
      if (spot.leader && spot.from) {
        const line = leaderOf(spot)
        if (leaders.some(([a, b]) => segmentsCross(line[0], line[1], a, b))) return false
        if (taken.some((other) => segmentHitsRect(line[0], line[1], other))) return false
        if (ui.some((other) => segmentHitsRect(line[0], line[1], other, 0))) return false
      }
      return !near(rect, c.id).some((item) => {
        if (overSolids && item.kind === 'solid') return false
        return item.polygon ? rectHitsPolygon(rect, item.polygon, 1) : rectsOverlap(rect, item.rect, 0)
      })
    }
    // A zone's name keeps clear of solids when it can; a zone is a floor with things standing on it, so as a last resort it may lie over them.
    const spot = c.must ? spots[0] : spots.find((one) => free(one)) || (c.zone ? spots.find((one) => free(one, true)) : undefined)
    if (!spot) continue
    taken.push(screenRect(spot))
    if (spot.leader && spot.from) leaders.push(leaderOf(spot))
    shown.set(c.id, { at: spot.at, dx: spot.cx, dy: spot.cy, leader: spot.leader && Boolean(spot.from), from: spot.from, to: spot.to })
  }
  return shown
}
