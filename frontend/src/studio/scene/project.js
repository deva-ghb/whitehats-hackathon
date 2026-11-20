// The isometric projection of the scene, ported from the website's drawing kit (website/brand/draw.py, class Iso).
//
// Ground coordinates are the clone's own (pixels in `layout`). One drawing unit is UNIT_X ground pixels along x and UNIT_Y along y. The viewer looks from
// +x, +y and above: x runs right and down the screen, y runs left and down, z runs up. Everything stands on one plane,
// z = BASE (the top of the platform), so a pointer over the picture always maps to exactly one ground point.
//
// Two spaces are used. "Scene space" is the drawing at zoom 1 (SCALE screen pixels per unit). The screen is scene space
// scaled by the view's zoom and moved by its offset, so panning and zooming never regenerate a shape.

// Ground pixels in one drawing unit, along x and along y. They differ on purpose: a card on the flat plan is 232 by 72 pixels, three
// times as wide as deep, while a solid stands about as wide as it is deep. Counting x for less brings the two views into step, so a
// row of cards on the plan is a row of solids the scene can draw at a size the eye reads, and the ground is as dense as the plan is.
export const UNIT_X = 160
export const UNIT_Y = 96
export const SCALE = 40            // screen pixels in one drawing unit at zoom 1
export const COS30 = 0.8660254037844386
export const BASE = 0.75           // height of the platform top, and of the plane everything stands on
export const MIN_ZOOM = 0.08
export const MAX_ZOOM = 3

const fmt = (value) => {
  const text = value.toFixed(1)
  return text.endsWith('.0') ? text.slice(0, -2) : text
}
export const num = fmt

/** Drawing units to scene space (the screen at zoom 1, before panning), for a point at height z. */
export function project(u, v, z = 0) {
  return [(u - v) * COS30 * SCALE, (u + v) * 0.5 * SCALE - z * SCALE]
}

/** Scene space to drawing units on the horizontal plane at height z. The exact inverse of `project`. */
export function unproject(x, y, z = 0) {
  const across = x / (COS30 * SCALE)
  const down = (y + z * SCALE) / (0.5 * SCALE)
  return [(across + down) / 2, (down - across) / 2]
}

/** A ground point (the clone's pixels) to scene space, standing on the plane at BASE plus `lift` units. */
export function groundToScene(gx, gy, lift = 0) {
  return project(gx / UNIT_X, gy / UNIT_Y, BASE + lift)
}

/** Scene space to a ground point, on the plane at BASE plus `lift` units. */
export function sceneToGround(x, y, lift = 0) {
  const [u, v] = unproject(x, y, BASE + lift)
  return { x: u * UNIT_X, y: v * UNIT_Y }
}

/** The view is `{ x, y, zoom }`: scene space times zoom, moved by (x, y), is the screen of the canvas. */
export const sceneToScreen = (view, x, y) => [x * view.zoom + view.x, y * view.zoom + view.y]
export const screenToScene = (view, px, py) => [(px - view.x) / view.zoom, (py - view.y) / view.zoom]

/** A pointer position on the canvas (pixels from its top left) to a ground point. */
export function screenToGround(view, px, py, lift = 0) {
  const [x, y] = screenToScene(view, px, py)
  return sceneToGround(x, y, lift)
}

export function groundToScreen(view, gx, gy, lift = 0) {
  const [x, y] = groundToScene(gx, gy, lift)
  return sceneToScreen(view, x, y)
}

export const clampZoom = (zoom) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom))

/** Zoom about a screen point, so the thing under the pointer stays under it. */
export function zoomAbout(view, px, py, zoom) {
  const next = clampZoom(zoom)
  const [x, y] = screenToScene(view, px, py)
  return { zoom: next, x: px - x * next, y: py - y * next }
}

/** The screen rectangle (scene space) that holds all these scene points. */
export function boundsOf(points) {
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const [x, y] of points) {
    x0 = Math.min(x0, x)
    y0 = Math.min(y0, y)
    x1 = Math.max(x1, x)
    y1 = Math.max(y1, y)
  }
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
}

/** A view that shows a scene rectangle in a canvas, centred, never zoomed in past `maxZoom`. */
export function fitView(bounds, canvas, { margin = 40, insets, maxZoom = 1.4, minZoom = MIN_ZOOM } = {}) {
  if (!bounds || !(bounds.width > 0) || !(bounds.height > 0) || canvas.width <= 0 || canvas.height <= 0) {
    return { x: canvas.width / 2, y: canvas.height / 2, zoom: 1 }
  }
  // `insets` (pixels taken at each edge, by floating panels) replaces the even margin when given
  const { top, bottom, left, right } = insets || { top: margin, bottom: margin, left: margin, right: margin }
  const open = { x: left, y: top, width: Math.max(canvas.width - left - right, 1), height: Math.max(canvas.height - top - bottom, 1) }
  const fit = Math.min(open.width / bounds.width, open.height / bounds.height)
  const zoom = Math.min(maxZoom, Math.max(minZoom, fit))
  return {
    zoom,
    x: open.x + open.width / 2 - (bounds.x + bounds.width / 2) * zoom,
    y: open.y + open.height / 2 - (bounds.y + bounds.height / 2) * zoom,
  }
}
