// Which solid each kind of node is drawn as. The mapping is the one written in the STUDIO.2 journal entry.
//
// Every recipe draws into the cell a node occupies on the ground (NODE_W by NODE_H pixels, so 3.6 by 1.1 drawing units),
// from the cell's back corner, which is the local origin. It returns the markup, the convex outline that hit testing uses,
// the top of the solid (where a sense point sits and connections end), and where its label hangs.
import { NODE_H, NODE_W } from '../geometry.js'
import { UNIT_X, UNIT_Y, project } from './project.js'
import * as shape from './shapes.js'
import { hull } from './hit.js'

export const CELL = { w: NODE_W / UNIT_X, d: NODE_H / UNIT_Y }

// The hatch that goes on a side face, by tone, so a tinted solid keeps its tint.
const HATCH = { w: 'sc-hatch', s: 'sc-hatch-t', c: 'sc-hatch-c', d: null, g: null }
export const LIFT = 3.4   // a person floats at least this far above its place, in drawing units

/**
 * kind: the solid. outside: where the automatic layout puts it when it has no group ('left' for what comes in from the
 * outside world, 'right' for what the estate reaches out to, otherwise inside the platform).
 */
export const SPECS = {
  backend: { kind: 'box', w: 1.2, d: 0.8, h: 1.8, hatch: true },
  frontend: { kind: 'box', w: 1.4, d: 0.8, h: 1.0, hatch: true },
  worker: { kind: 'box', w: 1.1, d: 0.8, h: 1.4, hatch: true },
  function: { kind: 'box', w: 0.9, d: 0.7, h: 1.2, hatch: true, glyph: 'bolt' },
  vm: { kind: 'stack', w: 1.2, d: 0.8, each: 0.34, count: 3, hatchRight: true },
  data: { kind: 'discs', r: 0.46, h: 1.5, count: 3 },
  object: { kind: 'discs', r: 0.5, h: 0.85, count: 2 },
  cache: { kind: 'discs', r: 0.38, h: 1.0, count: 2 },
  queue: { kind: 'stack', w: 1.4, d: 0.75, each: 0.22, count: 4 },
  edge: { kind: 'hex', r: 0.5, h: 1.3, outside: 'left' },
  person: { kind: 'person', outside: null },
  visitor: { kind: 'person', outside: 'left' },
  vault: { kind: 'box', w: 1.0, d: 0.8, h: 1.15, hatch: true, glyph: 'key' },
  repo: { kind: 'stack', w: 1.4, d: 0.8, each: 0.26, count: 3, hatchRight: true, outside: 'right' },
  pipeline: { kind: 'box', w: 1.4, d: 0.8, h: 0.5, topHatch: true, outside: 'right' },
  logs: { kind: 'box', w: 1.1, d: 0.8, h: 0.95, dots: true },
  saas: { kind: 'wedge', w: 1.4, d: 0.75, h: 0.9, outside: 'right' },
  control: { kind: 'pyramid', w: 0.85, h: 1.5, base: 's' },
  ai: { kind: 'box', w: 1.1, d: 0.8, h: 2.1, topTone: 'd', hatch: true },
  note: { kind: 'box', w: 1.3, d: 0.8, h: 0.14 },
  laptop: { kind: 'box', w: 1.0, d: 0.7, h: 0.4, outside: 'left' },
  generic: { kind: 'box', w: 1.1, d: 0.8, h: 0.9, base: 'g', ghost: true },
}

const PEOPLE = new Set(['identity.okta', 'identity.entra', 'identity.entra-external', 'identity.cognito', 'identity.iam-role', 'identity.managed-identity'])
const VAULTS = new Set(['identity.kms', 'identity.key-vault', 'identity.secrets-manager', 'identity.ssm-parameter-store'])
const BY_ID = {
  'service.backend': 'backend', 'service.frontend': 'frontend', 'service.worker': 'worker', 'service.function': 'function',
  'compute.vm': 'vm', 'data.s3': 'object', 'data.blob': 'object', 'data.elasticache': 'cache', 'data.redis': 'cache',
  'endpoint.customer': 'visitor', 'endpoint.staff': 'visitor', 'endpoint.laptop': 'laptop',
  'delivery.ecr': 'repo', 'delivery.acr': 'repo', 'generic.annotations': 'note', 'generic.whitehats': 'control',
}
const BY_FAMILY = {
  services: 'backend', compute: 'vm', data: 'data', messaging: 'queue', network: 'edge', identity: 'person', endpoints: 'visitor',
  code_and_delivery: 'pipeline', observability: 'logs', saas: 'saas', security_controls: 'control', ai: 'ai', annotations: 'note', whitehats: 'control',
}

/** The recipe name for a library type (its id, like "service.backend", and its family). An unknown type is drawn as a generic one. */
export function recipeName(typeId, family) {
  if (!typeId || !family) return 'generic'
  if (typeId.startsWith('generic.') && !BY_ID[typeId]) return 'generic'
  if (PEOPLE.has(typeId)) return 'person'
  if (VAULTS.has(typeId)) return 'vault'
  if (BY_ID[typeId]) return BY_ID[typeId]
  if (typeId.startsWith('repo.')) return 'repo'
  return BY_FAMILY[family] || 'generic'
}

const corners = (x, y, w, d, h, z = 0) => [[x, y, z], [x + w, y, z], [x + w, y + d, z], [x, y + d, z], [x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]]
const ring = (cx, cy, r, z, n = 14) => Array.from({ length: n }, (_, i) => [cx + Math.cos((i / n) * Math.PI * 2) * r, cy + Math.sin((i / n) * Math.PI * 2) * r, z])
const outlineOf = (points) => hull(points.map(([x, y, z]) => project(x, y, z)))

/** The faces a solid wears: the state tone, or the kind's own base tone when it has one and the state says nothing. */
export function toneFor(spec, state) {
  if (state === 'finding') return 'c'
  if (spec.base && spec.base !== 'g') return spec.base === 'd' && state === 'watched' ? 'd' : spec.base
  if (spec.base === 'g') return 'g'
  return state === 'watched' ? 's' : 'w'
}

/**
 * Draw one node. `detail` false draws the plain box of the same size (what the scene shows zoomed out).
 * Returns { markup, outline, top, bottom, height, floats } in local scene space.
 */
export function solid(name, { tone, detail = true, draft = false, tall = 1, lift = LIFT }) {
  const base = SPECS[name] || SPECS.generic
  // A taller solid is the same solid stretched: the busiest things in a clone stand highest (see model.js).
  const spec = tall === 1 || !base.h ? base : { ...base, h: base.h * tall }
  const ox = (CELL.w - (spec.w || spec.r * 2 || 1)) / 2
  const oy = (CELL.d - (spec.d || spec.r * 2 || 1)) / 2
  const hatch = HATCH[tone]
  const footing = draft && detail && spec.kind !== 'person' ? shape.draftBase({
    x: (CELL.w - (spec.w || spec.r * 2)) / 2, y: (CELL.d - (spec.d || spec.w || spec.r * 2)) / 2, w: spec.w || spec.r * 2, d: spec.d || spec.w || spec.r * 2 }) : ''
  const finish = (markup, points, topAt, bottomAt, height, extra = {}) => {
    markup = footing + markup
    const outline = outlineOf(points)
    return { markup, outline, top: project(...topAt), bottom: project(...bottomAt), height, floats: false, ...extra }
  }
  if (spec.kind === 'person') {
    const cx = CELL.w / 2
    const cy = CELL.d / 2
    const [px, py] = project(cx, cy, lift)
    const [gx, gy] = project(cx, cy, 0)
    const badge = Array.from({ length: 16 }, (_, i) => [px + Math.cos((i / 16) * Math.PI * 2) * 28, py + Math.sin((i / 16) * Math.PI * 2) * 28])
    const spot = Array.from({ length: 10 }, (_, i) => [gx + Math.cos((i / 10) * Math.PI * 2) * 9, gy + Math.sin((i / 10) * Math.PI * 2) * 5])
    return {
      markup: detail ? shape.person({ x: cx, y: cy, z: lift, tone }) : shape.person({ x: cx, y: cy, z: lift, drop: false, tone }),
      outline: hull([...badge, ...spot]), badge, top: [px, py + 22], bottom: [gx, gy + 5], nameAt: [px, py + 32], height: lift, floats: true,
    }
  }
  const slab = (w, d, h) => ({ x: ox, y: oy, w, d, h })
  if (!detail && spec.kind !== 'discs') {
    const w = spec.w || spec.r * 2
    const d = spec.d || spec.w || spec.r * 2
    const h = spec.kind === 'stack' ? spec.count * (spec.each + 0.05) : spec.h
    const at = { x: (CELL.w - w) / 2, y: (CELL.d - d) / 2 }
    return finish(shape.box({ ...at, w, d, h, tone, topTone: spec.topTone }), corners(at.x, at.y, w, d, h), [at.x + w / 2, at.y + d / 2, h], [at.x + w / 2, at.y + d, 0], h)
  }
  if (spec.kind === 'box') {
    const markup = shape.box({ ...slab(spec.w, spec.d, spec.h), tone, topTone: spec.topTone, right: spec.hatch || spec.hatchRight ? hatch : null, top: spec.topHatch ? hatch : spec.dots ? 'sc-dots' : null })
      + (spec.glyph === 'bolt' ? shape.bolt({ x: ox + spec.w / 2, y: oy + spec.d / 2, z: spec.h }) : '')
      + (spec.glyph === 'key' ? shape.keyhole({ x: ox + spec.w / 2, y: oy + spec.d / 2, z: spec.h }) : '')
    return finish(markup, corners(ox, oy, spec.w, spec.d, spec.h), [ox + spec.w / 2, oy + spec.d / 2, spec.h], [ox + spec.w / 2, oy + spec.d, 0], spec.h)
  }
  if (spec.kind === 'stack') {
    const height = spec.count * (spec.each + 0.05)
    const markup = shape.stack({ gap: 0.04, x: ox, y: oy, w: spec.w, d: spec.d, each: spec.each, count: spec.count, tone, topTone: tone === 'w' ? 's' : tone, hatchRight: spec.hatchRight ? hatch : null })
    return finish(markup, corners(ox, oy, spec.w, spec.d, height), [ox + spec.w / 2, oy + spec.d / 2, height], [ox + spec.w / 2, oy + spec.d, 0], height)
  }
  if (spec.kind === 'discs') {
    const cx = CELL.w / 2
    const cy = CELL.d / 2
    const markup = shape.discs({ x: cx, y: cy, r: spec.r, h: spec.h, count: spec.count, tone, topTone: tone === 'w' ? 'w' : tone })
    return finish(markup, [...ring(cx, cy, spec.r * 1.02, 0), ...ring(cx, cy, spec.r * 1.02, spec.h)], [cx, cy, spec.h], [cx, cy + spec.r, 0], spec.h)
  }
  if (spec.kind === 'hex') {
    const cx = CELL.w / 2
    const cy = CELL.d / 2
    const markup = shape.hexPrism({ cx, cy, r: spec.r, h: spec.h, tone: tone === 'w' ? 's' : tone })
    return finish(markup, [...ring(cx, cy, spec.r, 0, 6), ...ring(cx, cy, spec.r, spec.h, 6)], [cx, cy, spec.h], [cx, cy + spec.r, 0], spec.h)
  }
  if (spec.kind === 'wedge') {
    const markup = shape.wedge({ x: ox, y: oy, w: spec.w, d: spec.d, h: spec.h })
    return finish(markup, corners(ox, oy, spec.w, spec.d, spec.h), [ox + spec.w * 0.7, oy + spec.d / 2, spec.h * 0.7], [ox + spec.w / 2, oy + spec.d, 0], spec.h)
  }
  const w = spec.w
  const markup = shape.pyramid({ x: (CELL.w - w) / 2, y: (CELL.d - w) / 2, w, h: spec.h, tone })
  return finish(markup, corners((CELL.w - w) / 2, (CELL.d - w) / 2, w, w, spec.h * 0.5), [CELL.w / 2, CELL.d / 2, spec.h], [CELL.w / 2, CELL.d / 2 + w / 2, 0], spec.h)
}
