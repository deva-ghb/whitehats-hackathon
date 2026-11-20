// The parts the scene is drawn from, as small memoized components so a move or a selection repaints only what changed.
// Shapes arrive as SVG markup built by shapes.js and recipes.js (never from user text: names are drawn as text nodes).
import { memo } from 'react'
import { BASE, COS30, SCALE, UNIT_X, UNIT_Y, groundToScene, num } from './project.js'
import * as shape from './shapes.js'

const move = ([x, y]) => `translate(${num(x)} ${num(y)})`
const Html = ({ markup, ...props }) => <g {...props} dangerouslySetInnerHTML={{ __html: markup }} />

/** Hatching and the arrowhead, defined once for the whole scene. The colours come from classes, so themes work. */
export const Defs = memo(function Defs() {
  const pattern = (id, a, b) => (
    <pattern id={id} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="5" height="5" style={{ fill: `var(${a})` }} />
      <line x1="0" y1="0" x2="0" y2="5" style={{ stroke: `var(${b})`, strokeWidth: 1.1 }} />
    </pattern>
  )
  return (
    <defs>
      {pattern('sc-hatch', '--sc-hatch-a', '--sc-hatch-b')}
      {pattern('sc-hatch-t', '--sc-hatch-ta', '--sc-hatch-tb')}
      {pattern('sc-hatch-c', '--sc-hatch-ca', '--sc-hatch-cb')}
      <pattern id="sc-dots" width="9" height="9" patternUnits="userSpaceOnUse">
        <rect width="9" height="9" style={{ fill: 'var(--sc-w-t)' }} /><circle cx="4.5" cy="4.5" r="1.1" style={{ fill: 'var(--sc-ink)' }} />
      </pattern>
      <marker id="sc-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" markerUnits="userSpaceOnUse" orient="auto">
        <path d="M0 0 L8 4 L0 8 z" className="sc-arrow" />
      </marker>
    </defs>
  )
})

/** The platform: its private boundary (far glass walls), the white slab, and a label along the near left face. */
export const PlatformBack = memo(function PlatformBack({ platform }) {
  const w = platform.width / UNIT_X
  const d = platform.height / UNIT_Y
  const fence = shape.enclosure({ w, d, thick: BASE })
  return (
    <g className="sc-platform" transform={move(groundToScene(platform.x, platform.y))}>
      <Html markup={fence.back + shape.platform({ w, d, thick: BASE })} />
    </g>
  )
})

export const PlatformFront = memo(function PlatformFront({ platform, label, fontPx }) {
  const w = platform.width / UNIT_X
  const d = platform.height / UNIT_Y
  const fence = shape.enclosure({ w, d, thick: BASE })
  return (
    <g className="sc-rim" transform={move(groundToScene(platform.x, platform.y))}>
      <Html markup={fence.front} />
      {fontPx > 0 && (
        <g transform={shape.leftFaceTransform(PLATFORM_LABEL_AT, d, -BASE + 0.16)} aria-hidden="true">
          <text className="sc-platform-label sc-fixed-text" style={{ fontSize: `${fontPx.toFixed(1)}px` }}>{label}</text>
        </g>
      )}
    </g>
  )
})

const PLATFORM_LABEL_AT = 0.5     // how far along the platform's front left face its name starts, in drawing units
const LABEL_EM = 0.8              // the width of one letter of the platform's name, as a share of its size (it is letter spaced)

/**
 * The platform's name along the slab's front left face: how big it is at a zoom, and the parallelogram it covers on screen.
 * The face is as tall as the slab is thick, so the name shrinks with the picture and goes when it would be too small to read.
 * Returns { fontPx, width, from } with `from` the scene point it starts at, or fontPx 0 when it is left out.
 */
export function platformLabelPlan(platform, label, zoom) {
  const face = (platform.width / UNIT_X) * COS30 * SCALE * zoom
  const fontPx = Math.min(13, BASE * SCALE * zoom * 0.6)
  const width = label.length * fontPx * LABEL_EM
  if (fontPx < 11 || width > face - PLATFORM_LABEL_AT * COS30 * SCALE * zoom - 16) return { fontPx: 0, width: 0, from: null }
  return { fontPx, width, from: groundToScene(platform.x + PLATFORM_LABEL_AT * UNIT_X, platform.y + platform.height, -BASE + 0.16 + 0) }
}

export const ZoneFloor = memo(function ZoneFloor({ zone, offset, ariaLabel, selected }) {
  const w = zone.box.width / UNIT_X
  const d = zone.box.height / UNIT_Y
  const at = [zone.origin[0] + offset[0], zone.origin[1] + offset[1]]
  return (
    <g className={`sc-zone ${zone.draft ? 'draft' : ''}`} transform={move(at)} tabIndex={0} role="button" aria-label={ariaLabel} aria-pressed={selected}
      data-scene-item="" data-id={zone.id}>
      <Html markup={shape.zoneFloor({ w, d, wall: zone.wall })} />
    </g>
  )
})

export const ZoneRim = memo(function ZoneRim({ zone, offset }) {
  const at = [zone.origin[0] + offset[0], zone.origin[1] + offset[1]]
  return <Html className="sc-rim" transform={move(at)} markup={shape.zoneRim({ w: zone.box.width / UNIT_X, d: zone.box.height / UNIT_Y, wall: zone.wall })} />
})

export const SolidShape = memo(function SolidShape({ solid, offset, ariaLabel, selected, plain, link }) {
  const at = [solid.origin[0] + offset[0], solid.origin[1] + offset[1]]
  return (
    <Html className={`sc-solid ${solid.draft ? 'draft' : ''} ${plain ? 'plain' : ''} ${link ? `link-${link}` : ''}`} transform={move(at)}
      tabIndex={0} role="button" aria-label={ariaLabel} aria-pressed={selected} data-scene-item="" data-id={solid.id} markup={solid.markup} />
  )
})

/** A connection: a dashed line between the tops of two things, with an arrowhead at the end it points to. */
export const EdgeShape = memo(function EdgeShape({ edge, from, to, selected, related, ariaLabel }) {
  const [x1, y1] = from
  const [x2, y2] = to
  const length = Math.hypot(x2 - x1, y2 - y1) || 1
  const pull = Math.min(5, length / 3)
  const ex = x2 - ((x2 - x1) / length) * pull
  const ey = y2 - ((y2 - y1) / length) * pull
  return (
    <g tabIndex={0} role="button" aria-label={ariaLabel} aria-pressed={selected} data-scene-edge={edge.id}>
      <path className={`sc-edge ${selected ? 'on' : ''} ${related ? 'rel' : ''}`} d={`M${num(x1)} ${num(y1)} L${num(ex)} ${num(ey)}`} markerEnd="url(#sc-arrow)" />
    </g>
  )
})

/** A sense point sitting on a surface: a target ring with the WhiteHat robot beside it. */
export const SenseShape = memo(function SenseShape({ at, status }) {
  return <Html transform={move(at)} markup={shape.sensePoint(0, 0, status)} />
})

/**
 * A name, always 12 px on screen. `place` says where its middle is from its anchor (see labels.js); a name that stands off has a
 * thin leader line back to its solid. Zone names sit above their corner.
 */
export const Label = memo(function Label({ at, place, text, sub, zone, selected }) {
  const { dx = 0, dy = 0, leader, from, to } = place || {}
  return (
    <g className={`sc-label ${zone ? 'zone' : ''} ${selected ? 'on' : ''}`} transform={move(at)} aria-hidden="true">
      <g className="sc-fixed">
        {leader && <line className="sc-leader" x1={from[0]} y1={from[1]} x2={to[0]} y2={to[1]} />}
        <text x={dx} y={dy + (sub ? -4 : 4.5)}>{text}</text>
        {sub && <text className="sub" x={dx} y={dy + 12}>{sub}</text>}
      </g>
    </g>
  )
})
