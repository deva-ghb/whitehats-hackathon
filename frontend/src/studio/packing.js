// Packing rectangles tightly: the containers of a clone and the block of nodes beside them, into a block of the wanted shape. Pure, no drawing.
// MaxRects with the best-short-side-fit rule: each rectangle goes in the free space it fits most snugly, so a small
// container tucks in beside a large one instead of starting a new row under it.

/** Does a contain b? Rectangles are {x, y, width, height}. */
const holds = (a, b) => b.x >= a.x && b.y >= a.y && b.x + b.width <= a.x + a.width && b.y + b.height <= a.y + a.height
const meets = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

/** Cut every free rectangle that the placed one touches into the pieces that remain, then drop pieces inside others. */
function cut(free, placed) {
  const next = []
  for (const rect of free) {
    if (!meets(rect, placed)) { next.push(rect); continue }
    if (placed.x > rect.x) next.push({ ...rect, width: placed.x - rect.x })
    if (placed.x + placed.width < rect.x + rect.width) next.push({ ...rect, x: placed.x + placed.width, width: rect.x + rect.width - placed.x - placed.width })
    if (placed.y > rect.y) next.push({ ...rect, height: placed.y - rect.y })
    if (placed.y + placed.height < rect.y + rect.height) next.push({ ...rect, y: placed.y + placed.height, height: rect.y + rect.height - placed.y - placed.height })
  }
  return next.filter((rect, i) => !next.some((other, j) => i !== j && holds(other, rect) && (!holds(rect, other) || j < i)))
}

/** Pack into a bin `limit` wide with no height limit, in the order given. Returns { at, width, height } (cells without their gap). */
function packInto(cells, limit, gap) {
  let free = [{ x: 0, y: 0, width: limit, height: 1e7 }]
  const at = new Map()
  let width = 0
  let height = 0
  for (const cell of cells) {
    const w = cell.width + gap.x
    const h = cell.height + gap.y
    let best = null
    for (const rect of free) {
      if (rect.width < w || rect.height < h) continue
      // Snug fit on the short side, and nearer the top and the left on a tie, so the picture fills from its corner.
      const score = [Math.min(rect.width - w, rect.height - h), rect.y, rect.x]
      if (!best || score[0] < best.score[0] || (score[0] === best.score[0] && (score[1] < best.score[1] || (score[1] === best.score[1] && score[2] < best.score[2])))) best = { rect, score }
    }
    if (!best) return null
    const placed = { x: best.rect.x, y: best.rect.y, width: w, height: h }
    at.set(cell.id, { x: placed.x, y: placed.y })
    width = Math.max(width, placed.x + cell.width)
    height = Math.max(height, placed.y + cell.height)
    free = cut(free, placed)
  }
  return { at, width, height }
}

/** How much a shape is penalised for being off the wanted width to height ratio; one unit of ln(ratio) costs this share of the area. */
const OFF_SHAPE = 1.2

/**
 * Pack cells {id, width, height} into the smallest block that is also close to the wanted shape (`aspect` is width over height).
 * Several widths are tried; the cheapest wins: area, made dearer the further the block is from the wanted shape. Deterministic.
 */
export function packRects(cells, { gap, aspect }) {
  if (!cells.length) return { width: 0, height: 0, at: new Map() }
  const widest = Math.max(...cells.map((cell) => cell.width)) + gap.x     // a cell is placed with its gap, so the narrowest bin holds the widest cell and one gap
  const total = cells.reduce((sum, cell) => sum + cell.width + gap.x, 0)
  const area = cells.reduce((sum, cell) => sum + (cell.width + gap.x) * (cell.height + gap.y), 0)
  const guess = Math.sqrt(area * aspect)
  const limits = new Set([Math.round(widest)])
  for (let k = 0.5; k <= 2.2; k += 0.1) limits.add(Math.round(Math.max(widest, Math.min(total, guess * k))))
  let best = null
  for (const limit of limits) {
    const made = packInto(cells, limit, gap)
    if (!made) continue
    const cost = made.width * made.height * (1 + OFF_SHAPE * Math.abs(Math.log(made.width / made.height / aspect)))
    if (!best || cost < best.cost - 1e-6) best = { cost, made }
  }
  return best.made
}
