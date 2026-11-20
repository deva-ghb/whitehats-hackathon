// Where things stand in the scene: the places for a clone that has none stored, and the platform under the rest.
// Pure functions of a clone document, so they test without a browser. Positions are the clone's own ground pixels.
import { EMPTY_ESTATE, outsideSide, placesFor } from '../autolayout.js'
import { GRID, parentOf } from '../geometry.js'
import { UNIT_X, UNIT_Y } from './project.js'

const PLATFORM_MARGIN = { x: 72, y: 56 }   // the ground the platform keeps around what stands on it
export const EMPTY_PLATFORM = EMPTY_ESTATE

const ceilToGrid = (value) => Math.ceil(value / GRID) * GRID
const everyItem = (document) => [...(document.groups || []), ...(document.nodes || [])]

export { outsideSide }

// On an isometric picture the width and the depth of the ground both add to how much screen it takes, so the smallest picture is the cheapest, and a squarer one is kept over a long strip.
// Solids stand taller than a card and carry names, so the scene keeps more room between them than the plan.
export const SCENE_SPACING = { gap: { x: 40, y: 64 }, outside: { x: 168, y: 56 }, outsideAt: 'corners', cost: (width, height) => width / UNIT_X + height / UNIT_Y + 0.25 * Math.abs(width / UNIT_X - height / UNIT_Y) }

/** The absolute box of every item as the scene draws it: the stored places, with the automatic ones filling any gap. */
export const sceneBoxes = (document, types) => (document ? placesFor(document, types, SCENE_SPACING) : new Map())

/**
 * The platform: the ground under everything that stands inside it. It wraps the top level groups and the top level nodes
 * that are not meant to stand outside, with a margin. An empty clone gets a blank platform to drop onto.
 */
export function platformBox(document, boxes, types) {
  const isGroup = new Set((document?.groups || []).map((group) => group.id))
  const inside = everyItem(document || { nodes: [], groups: [] }).filter((item) => !parentOf(item) && boxes.has(item.id)
    && (isGroup.has(item.id) || !outsideSide(item, types)))
  if (!inside.length) {
    const anyone = [...boxes.values()]
    if (!anyone.length) return { ...EMPTY_PLATFORM }
    return { ...EMPTY_PLATFORM }
  }
  const held = inside.map((item) => boxes.get(item.id))
  const x = Math.floor((Math.min(...held.map((box) => box.x)) - PLATFORM_MARGIN.x) / GRID) * GRID
  const y = Math.floor((Math.min(...held.map((box) => box.y)) - PLATFORM_MARGIN.y) / GRID) * GRID
  const right = Math.max(...held.map((box) => box.x + box.width)) + PLATFORM_MARGIN.x
  const bottom = Math.max(...held.map((box) => box.y + box.height)) + PLATFORM_MARGIN.y
  return { x, y, width: ceilToGrid(right - x), height: ceilToGrid(bottom - y) }
}
