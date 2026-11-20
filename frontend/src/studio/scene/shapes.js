// Reusable SVG drawing functions for the isometric scene, ported from the website's drawing kit (website/brand/draw.py).
//
// Each function returns SVG markup as a string, in scene space, relative to a local origin (a footprint's back corner,
// standing on the platform plane). Coordinates are drawing units: x runs right and down, y runs left and down, z runs up.
// Colour comes from CSS classes (scene.css), never from the markup, so one drawing serves the light and the dark theme.
// A tone is a set of three faces: w white, s soft teal, d deep teal, c finding (red tint), g glass.
import { COS30, SCALE, num, project } from './project.js'

const pt = (x, y, z = 0) => {
  const [X, Y] = project(x, y, z)
  return `${num(X)},${num(Y)}`
}
const pts = (list) => list.map(([x, y, z]) => pt(x, y, z)).join(' ')

/** A closed shape drawn as one polygon on any plane. `fill` is a tone-face class or a pattern reference. */
export function plane(corners, cls = '', fill = null) {
  const paint = fill && fill.startsWith('url(') ? ` fill="${fill}"` : ''
  return `<polygon points="${pts(corners)}" class="${cls}"${paint}/>`
}

const face = (corners, tone, side, pattern) => (pattern
  ? `<polygon points="${pts(corners)}" fill="url(#${pattern})"/>`
  : `<polygon points="${pts(corners)}" class="${side}-${tone}"/>`)

/**
 * A rectangular solid. `right` and `top` may name a hatch pattern ("sc-hatch" or "sc-hatch-t") to draw on that face, as the
 * site does for a side that "does something" (a gateway, a repository).
 */
export function box({ x = 0, y = 0, z = 0, w, d, h, tone = 'w', right = null, top = null, left = null, topTone = tone }) {
  return `<g>${face([[x, y + d, z], [x + w, y + d, z], [x + w, y + d, z + h], [x, y + d, z + h]], tone, 'l', left)}`
    + `${face([[x + w, y, z], [x + w, y + d, z], [x + w, y + d, z + h], [x + w, y, z + h]], tone, 'r', right)}`
    + `${face([[x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]], topTone, 't', top)}</g>`
}

/** A round solid: its top and a curved side. (x, y) is the middle of the circle on the ground, r its radius in units. */
export function cylinder({ x, y, z = 0, r, h, tone = 'w', hatch = null }) {
  const [cx, top] = project(x, y, z + h)
  const [, bottom] = project(x, y, z)
  const rx = r * SCALE * 1.2247
  const ry = r * SCALE * 0.7071
  const side = hatch ? `fill="url(#${hatch})"` : `class="l-${tone}"`
  return `<g><path d="M${num(cx - rx)} ${num(top)} V ${num(bottom)} A ${num(rx)} ${num(ry)} 0 0 0 ${num(cx + rx)} ${num(bottom)} V ${num(top)}" ${side}/>`
    + `<ellipse cx="${num(cx)}" cy="${num(top)}" rx="${num(rx)}" ry="${num(ry)}" class="t-${tone}"/></g>`
}

/** A pyramid on a square base. */
export function pyramid({ x = 0, y = 0, z = 0, w, h, tone = 'w' }) {
  const apex = [x + w / 2, y + w / 2, z + h]
  return `<g><polygon points="${pts([[x, y + w, z], [x + w, y + w, z], apex])}" class="l-${tone}"/>`
    + `<polygon points="${pts([[x + w, y, z], [x + w, y + w, z], apex])}" class="r-${tone}"/></g>`
}

/**
 * A six sided prism, the shape the site gives a public edge. Only the faces turned to the viewer are drawn, and the one
 * facing right is hatched. (cx, cy) is the middle on the ground; r the radius to a corner, in units.
 */
export function hexPrism({ cx, cy, z = 0, r, h, tone = 's' }) {
  const angles = [0, 1, 2, 3, 4, 5].map((k) => ((15 + k * 60) * Math.PI) / 180)
  const ring = angles.map((a) => [cx + Math.cos(a) * r, cy + Math.sin(a) * r])
  const sides = []
  for (let i = 0; i < 6; i += 1) {
    const a = ring[i]
    const b = ring[(i + 1) % 6]
    const mid = angles[i] + Math.PI / 6
    if (Math.cos(mid) + Math.sin(mid) <= 0.05) continue
    const hatched = Math.cos(mid) > Math.sin(mid)
    sides.push(`<polygon points="${pts([[...a, z], [...b, z], [...b, z + h], [...a, z + h]])}" ${hatched ? 'fill="url(#sc-hatch)"' : 'class="l-w"'}/>`)
  }
  return `<g>${sides.join('')}<polygon points="${pts(ring.map(([x, y]) => [x, y, z + h]))}" class="t-${tone} accent"/></g>`
}

/** A wedge rising towards its right end: the small paper-plane solid the site uses for a partner. */
export function wedge({ x = 0, y = 0, z = 0, w, d, h }) {
  return `<g><polygon points="${pts([[x, y + d, z], [x + w, y + d, z], [x + w, y + d, z + h]])}" class="l-w"/>`
    + `<polygon points="${pts([[x + w, y, z], [x + w, y + d, z], [x + w, y + d, z + h], [x + w, y, z + h]])}" class="r-s"/>`
    + `<polygon points="${pts([[x, y, z], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z]])}" fill="url(#sc-hatch)"/></g>`
}

/** Plates stacked with a gap between them, the top one tinted. `count` plates of height `each`. */
export function stack({ x = 0, y = 0, z = 0, w, d, each, count, gap = 0.05, tone = 'w', topTone = 's', hatchRight = null }) {
  let out = ''
  for (let i = 0; i < count; i += 1) {
    out += box({ x, y, z: z + i * (each + gap), w, d, h: each, tone: i === count - 1 ? topTone : tone, right: i === count - 1 ? null : hatchRight })
  }
  return `<g>${out}</g>`
}

/** Discs stacked on one another, the site's data store. */
export function discs({ x, y, z = 0, r, h, count = 3, tone = 'w', topTone = tone }) {
  const each = h / count
  let out = ''
  for (let i = 0; i < count; i += 1) {
    out += cylinder({ x, y, z: z + i * each, r, h: each - 0.04, tone: i === count - 1 ? topTone : tone })
  }
  return `<g>${out}</g>`
}

/** A lightning bolt lying on a top face, for a function. Centred on (x, y) at height z. */
export function bolt({ x, y, z }) {
  const [cx, cy] = project(x, y, z)
  return `<polygon class="glyph" points="${num(cx + 1)},${num(cy - 9)} ${num(cx - 6)},${num(cy + 1)} ${num(cx - 1)},${num(cy + 1)} ${num(cx - 3)},${num(cy + 9)} ${num(cx + 6)},${num(cy - 2)} ${num(cx + 1)},${num(cy - 2)}"/>`
}

/** A key lying on a top face, for something that holds a secret. */
export function keyhole({ x, y, z }) {
  const [cx, cy] = project(x, y, z)
  return `<g class="glyph"><circle cx="${num(cx)}" cy="${num(cy - 2)}" r="3.4"/><path d="M${num(cx - 1.4)} ${num(cy + 1)} l-2 7 h6.8 l-2 -7z"/></g>`
}

/** A teal disc with a white person in it: who signs in. Hangs at (x, y, z) with a dashed line down to the ground. */
export function person({ x, y, z, drop = true }) {
  const [cx, cy] = project(x, y, z)
  const [gx, gy] = project(x, y, 0)
  const r = 19
  return `<g>${drop ? `<line class="drop" x1="${num(cx)}" y1="${num(cy + r + 3)}" x2="${num(gx)}" y2="${num(gy)}"/>`
    + `<ellipse class="spot" cx="${num(gx)}" cy="${num(gy)}" rx="10" ry="5.5"/>` : ''}`
    + `<circle class="halo" cx="${num(cx)}" cy="${num(cy)}" r="${num(r * 1.6)}"/>`
    + `<circle class="badge" cx="${num(cx)}" cy="${num(cy)}" r="${r}"/>`
    + `<circle class="figure" cx="${num(cx)}" cy="${num(cy - r * 0.26)}" r="${num(r * 0.24)}"/>`
    + `<path class="figure" d="M${num(cx - r * 0.46)} ${num(cy + r * 0.5)} a ${num(r * 0.46)} ${num(r * 0.42)} 0 0 1 ${num(r * 0.92)} 0 z"/></g>`
}

/** The small WhiteHat robot head the site draws beside a sense point (agent_svg in draw.py). (cx, cy) is its middle on screen. */
export function robot(cx, cy, r = 10, mood = 'happy') {
  const w = r * 1.8
  const h = r * 1.4
  const x0 = cx - w / 2
  const y0 = cy - h / 2 + r * 0.15
  const tip = [cx + r * 0.1, y0 - r * 0.5]
  const mouth = mood === 'happy'
    ? `M${num(cx - r * 0.3)} ${num(cy + r * 0.42)} Q${num(cx)} ${num(cy + r * 0.62)} ${num(cx + r * 0.3)} ${num(cy + r * 0.42)}`
    : mood === 'asking'
      ? `M${num(cx - r * 0.22)} ${num(cy + r * 0.5)} h${num(r * 0.44)}`
      : `M${num(cx - r * 0.3)} ${num(cy + r * 0.56)} Q${num(cx)} ${num(cy + r * 0.36)} ${num(cx + r * 0.3)} ${num(cy + r * 0.56)}`
  return `<g class="robot ${mood}"><line class="stroke" x1="${num(cx)}" y1="${num(y0)}" x2="${num(tip[0])}" y2="${num(tip[1])}"/>`
    + `<circle class="tip" cx="${num(tip[0])}" cy="${num(tip[1] - 1.6)}" r="${num(Math.max(1.8, r * 0.15))}"/>`
    + `<line class="stroke" x1="${num(x0 - r * 0.22)}" y1="${num(cy + r * 0.15)}" x2="${num(x0)}" y2="${num(cy + r * 0.15)}"/>`
    + `<line class="stroke" x1="${num(x0 + w)}" y1="${num(cy + r * 0.15)}" x2="${num(x0 + w + r * 0.22)}" y2="${num(cy + r * 0.15)}"/>`
    + `<rect class="head" x="${num(x0)}" y="${num(y0)}" width="${num(w)}" height="${num(h)}"/>`
    + `<circle class="eye" cx="${num(cx - r * 0.36)}" cy="${num(cy + r * 0.02)}" r="${num(Math.max(1.5, r * 0.13))}"/>`
    + `<circle class="eye" cx="${num(cx + r * 0.36)}" cy="${num(cy + r * 0.02)}" r="${num(Math.max(1.5, r * 0.13))}"/>`
    + `<path class="mouth" d="${mouth}"/></g>`
}

/**
 * A sense point on a surface: a target ring with the small robot beside it. (cx, cy) is where it sits, on screen.
 * accepted: a solid dot in two rings and a smiling robot. suggested: a hollow, dashed ring that pulses and a robot asking.
 * rejected: a grey ring with a bar through it and a robot that is not smiling.
 */
export function sensePoint(cx, cy, status) {
  const mood = status === 'accepted' ? 'happy' : status === 'rejected' ? 'sad' : 'asking'
  const ring = status === 'accepted'
    ? `<circle class="halo" cx="${num(cx)}" cy="${num(cy)}" r="13"/><circle class="ring" cx="${num(cx)}" cy="${num(cy)}" r="9.5"/>`
      + `<circle class="dot" cx="${num(cx)}" cy="${num(cy)}" r="5.8"/>`
    : status === 'rejected'
      ? `<circle class="ring" cx="${num(cx)}" cy="${num(cy)}" r="9.5"/><circle class="dot" cx="${num(cx)}" cy="${num(cy)}" r="4"/>`
        + `<line class="bar" x1="${num(cx - 10)}" y1="${num(cy + 10)}" x2="${num(cx + 10)}" y2="${num(cy - 10)}"/>`
      : `<circle class="halo pulse" cx="${num(cx)}" cy="${num(cy)}" r="13"/><circle class="ring" cx="${num(cx)}" cy="${num(cy)}" r="9.5"/>`
        + `<circle class="dot" cx="${num(cx)}" cy="${num(cy)}" r="3.6"/>`
  return `<g class="sense ${status}">${ring}${robot(cx + 27, cy - 18, 12.5, mood)}</g>`
}

// ---------------------------------------------------------------- the ground: platform, zones

/** The white slab the whole enterprise stands on: w by d units, `thick` high, its top at z = 0 and a faint grid on it. */
export function platform({ w, d, thick, gridEvery = 2 }) {
  const slab = box({ x: 0, y: 0, z: -thick, w, d, h: thick, tone: 'w' })
  let grid = ''
  for (let i = 1; i * gridEvery < w; i += 1) grid += `M${pt(i * gridEvery, 0, 0)} L${pt(i * gridEvery, d, 0)}`
  for (let j = 1; j * gridEvery < d; j += 1) grid += `M${pt(0, j * gridEvery, 0)} L${pt(w, j * gridEvery, 0)}`
  return `<g>${slab}<path class="grid" d="${grid}"/></g>`
}

/**
 * A zone: a dashed ground plane with low glass walls on the two far sides, and a dashed rim on the two near sides that
 * is drawn after what stands inside (see `zoneRim`).
 */
export function zoneFloor({ w, d, wall }) {
  const far = (x, y, ww, dd) => box({ x, y, z: 0, w: ww, d: dd, h: wall, tone: 'g' })
  return `<g><polygon class="zone-floor" points="${pts([[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0]])}"/>`
    + `${far(0, 0, w, 0.06)}${far(0, 0, 0.06, d)}</g>`
}

export function zoneRim({ w, d, wall }) {
  const line = (a, b) => `M${pt(...a)} L${pt(...b)}`
  return `<path class="zone-rim" d="${line([w, 0, wall], [w, d, wall])}${line([0, d, wall], [w, d, wall])}`
    + `${line([w, 0, 0], [w, 0, wall])}${line([w, d, 0], [w, d, wall])}${line([0, d, 0], [0, d, wall])}"/>`
}

/** Where text lies along the near left face of a slab or zone, reading along x: the transform the site uses (text_left). */
export function leftFaceTransform(x, y, z) {
  const [X, Y] = project(x, y, z)
  return `matrix(${COS30} 0.5 0 1 ${num(X)} ${num(Y)})`
}

/** The ghost of an item being placed: its footprint as a dashed outline on the ground. */
export function footprint({ x, y, w, d }) {
  return `<polygon class="ghost" points="${pts([[x, y, 0], [x + w, y, 0], [x + w, y + d, 0], [x, y + d, 0]])}"/>`
}

/**
 * The private boundary around the whole platform, as on the site: a dashed ground line and two tall glass walls on the far
 * sides (`back`), and on the near sides a dashed rim at the top of the wall with posts down to the ground (`front`), which is
 * drawn over what stands inside. Local coordinates: the platform's top is z = 0 and its slab reaches down to -thick.
 */
export function enclosure({ w, d, thick, pad = 0.35, wall = 1.4 }) {
  const lo = -pad
  const z0 = -thick
  const top = z0 + wall + thick
  const glass = (x, y, ww, dd) => box({ x, y, z: z0, w: ww, d: dd, h: wall + thick, tone: 'g' })
  const line = (a, b) => `M${pt(...a)} L${pt(...b)}`
  return {
    back: `<g><polygon class="rim-ground" points="${pts([[lo, lo, z0], [w + pad, lo, z0], [w + pad, d + pad, z0], [lo, d + pad, z0]])}"/>`
      + `${glass(lo, lo, w + pad * 2, 0.06)}${glass(lo, lo, 0.06, d + pad * 2)}</g>`,
    front: `<path class="rim" d="${line([w + pad, lo, top], [w + pad, d + pad, top])}${line([lo, d + pad, top], [w + pad, d + pad, top])}`
      + `${line([w + pad, lo, z0], [w + pad, lo, top])}${line([w + pad, d + pad, z0], [w + pad, d + pad, top])}${line([lo, d + pad, z0], [lo, d + pad, top])}"/>`,
  }
}

/** The dashed footprint under a node that is still a Draft: the solid itself stays crisp, the unfinished part is the ground it will take. */
export function draftBase({ x, y, w, d, margin = 0.14 }) {
  return `<polygon class="draft-base" points="${pts([[x - margin, y - margin, 0], [x + w + margin, y - margin, 0], [x + w + margin, y + d + margin, 0], [x - margin, y + d + margin, 0]])}"/>`
}
