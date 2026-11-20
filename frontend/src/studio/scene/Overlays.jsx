// What sits over the scene besides the drawing: the card on hover, the zoom buttons, the sense point preview, the marks
// that show what is selected, hovered or being dragged, and the alert marks of connector problems.
import { useState } from 'react'
import { Icon } from '../../components/ui'
import { NEW_GROUP, NODE_H, NODE_W } from '../geometry'
import { groundToScene } from './project'

const GROUND = (box) => [[box.x, box.y], [box.x + box.width, box.y], [box.x + box.width, box.y + box.height], [box.x, box.y + box.height]]
const ground = (corners) => corners.map(([x, y]) => groundToScene(x, y).map((n) => n.toFixed(1)).join(',')).join(' ')
export const outlinePoints = (outline) => outline.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')

/** The plain-words card, like the one on the website: what it is, its name, why it matters, and where it stands. */
export function Tooltip({ card, at, size, tipRef }) {
  return (
    <div ref={tipRef} className="sc-tip" style={{ left: Math.min(at.x + 16, Math.max(0, size.width - 296)), top: at.y + 18 }} aria-hidden="true">
      <b>{card.title}</b>
      <span className="name">{card.name}</span>
      {card.words && <span>{card.words}</span>}
      {card.state && <span className="state">{card.state}</span>}
    </div>
  )
}

const MinusIcon = () => (
  <svg className="ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M5 12h14" /></svg>
)

export function Controls({ onZoom, onFit }) {
  return (
    <div className="scene-controls" role="group" aria-label="Zoom">
      <button className="btn small" title="Zoom in (+)" aria-label="Zoom in" onClick={() => onZoom(1.25)}><Icon.plus size={16} /></button>
      <button className="btn small" title="Zoom out (-)" aria-label="Zoom out" onClick={() => onZoom(0.8)}><MinusIcon /></button>
      <button className="btn small" title="Fit to screen (0)" aria-label="Fit the scene to the screen" onClick={onFit}><Icon.fit size={16} /></button>
    </div>
  )
}

const KEY_STORE = 'wh-scene-key'
const KEY_ITEMS = [
  ['w', 'White solid', 'not watched yet'],
  ['s', 'Teal solid with a ring and a robot', 'watched: it has an accepted sense point'],
  ['c', 'Red solid', 'has a finding'],
  ['d', 'Tall deep teal solid', 'the most connected one in this clone'],
  ['x', 'Dashed footprint', 'a draft, still missing required details'],
]

/** How to read the picture: what each look means, in words. Open by default on a wide pane, and the choice is remembered. */
export function SceneKey({ wide }) {
  const [kept, setKept] = useState(() => {
    try { return window.localStorage.getItem(KEY_STORE) } catch { return null }
  })
  const open = kept === null ? wide : kept === 'open'
  const toggle = () => {
    const next = open ? 'closed' : 'open'
    setKept(next)
    try { window.localStorage.setItem(KEY_STORE, next) } catch { /* the choice lasts for this visit */ }
  }
  return (
    <div className="scene-key">
      <button className="btn small" aria-expanded={open} aria-controls="scene-key-list" onClick={toggle}>{open ? 'Hide key' : 'Key'}</button>
      {open && (
        <ul id="scene-key-list" aria-label="How to read the scene">
          {KEY_ITEMS.map(([tone, name, meaning]) => <li key={tone}><i className={tone} aria-hidden="true" /><b>{name}</b> {meaning}</li>)}
        </ul>
      )}
    </div>
  )
}

/** Outlines of what is hovered, selected or focused, the ends a connection may take, and the ghosts, rubber band and handles of a drag. */
export function UiLayer({ things, offsetOf, hovered, selected, focused, links, ghost, resizing, connecting, single, handles }) {
  const shift = (id) => `translate(${offsetOf(id).join(' ')})`
  return (
    <g className="sc-ui">
      {hovered && <polygon className="sc-hover" points={outlinePoints(hovered.outline)} transform={shift(hovered.id)} />}
      {selected.map((thing) => <polygon key={thing.id} className="sc-sel" points={outlinePoints(thing.outline)} transform={shift(thing.id)} />)}
      {focused && <polygon className="sc-focus" points={outlinePoints(focused.outline)} />}
      {links && [...links.entries()].map(([id, kind]) => (things.get(id)
        ? <polygon key={id} className={kind === 'typed' ? 'sc-link-typed' : 'sc-link-generic'} points={outlinePoints(things.get(id).outline)} /> : null))}
      {ghost && (() => {
        const size = ghost.group ? NEW_GROUP : { width: NODE_W, height: NODE_H }
        return <polygon className="ghost" points={ground(GROUND({ x: ghost.at.x - size.width / 2, y: ghost.at.y - size.height / 2, ...size }))} />
      })()}
      {resizing && <polygon className="ghost" points={ground(GROUND(resizing.box))} />}
      {connecting && single && <line className="sc-rubber" x1={single.top[0]} y1={single.top[1]} x2={connecting.at[0]} y2={connecting.at[1]} />}
      {single && !resizing && handles.map((handle) => (
        <g key={`${handle.kind}${handle.corner || ''}`} transform={`translate(${handle.x} ${handle.y})`}>
          <g className="sc-fixed">
            {handle.kind === 'connect'
              ? <circle className="sc-handle connect" cx={16} cy={0} r={7} />
              : <rect className="sc-handle" x={-5} y={-5} width={10} height={10} rx={2} />}
          </g>
        </g>
      ))}
    </g>
  )
}

/** A small amber mark on every node a connector says may be out of date. */
export function AlertMarks({ marks, things }) {
  return (
    <g>{(marks ? Object.keys(marks) : []).map((id) => {
      const thing = things.get(id)
      return thing && !thing.isZone ? (
        <g key={id} className="sc-alert" transform={`translate(${thing.bounds.x + thing.bounds.width} ${thing.bounds.y + 2})`} aria-hidden="true">
          <g className="sc-fixed"><circle r="8" /><text y="4">!</text></g>
        </g>
      ) : null
    })}</g>
  )
}
