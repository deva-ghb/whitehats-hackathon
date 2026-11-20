// SC.1: how a node's known package and image weaknesses read on screen. The server ranks them; this only words them.

export const REACH_SHORT = { 1: 'On an attack path', 2: 'Reachable', 3: 'Internal only' }

/** "2 known weaknesses, on an attack path", for a node's label and its badge's title. */
export function weaknessWords(weak) {
  const count = `${weak.count} known ${weak.count === 1 ? 'weakness' : 'weaknesses'}`
  return `${count}, ${weak.reach_words}`
}

/** The badge text: short enough for a card. */
export const badgeText = (weak) => `${weak.count} ${weak.count === 1 ? 'weakness' : 'weaknesses'}`

export const IMAGE_ROWS = 10

/** The rows to list: every package row, and each image's worst IMAGE_ROWS (rows come worst first); [image, hidden]. */
export function capImages(rows) {
  const seen = new Map()
  const shown = rows.filter((row) => {
    if (!row.image) return true
    seen.set(row.image, (seen.get(row.image) || 0) + 1)
    return seen.get(row.image) <= IMAGE_ROWS
  })
  const more = [...seen].filter(([, count]) => count > IMAGE_ROWS).map(([image, count]) => [image, count - IMAGE_ROWS])
  return { shown, more }
}

/** An image by name and tag; the digest stays in the tooltip. */
export const imageName = (image) => image.split('@')[0]
