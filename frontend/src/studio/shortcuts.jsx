// Keyboard equivalents for every editing action, and the list that shows them.
import { useEffect } from 'react'
import { Modal } from '../components/ui'

const NUDGE = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
export const STEP = 16
export const BIG_STEP = 64
// Places where a key belongs to the control, not the canvas: fields, open menus, dialogs, the zoom buttons.
const OWN_KEYS = 'input, textarea, select, [role="dialog"], [role="listbox"], .select, .select-content, .react-flow__controls, .react-flow__panel, .scene-controls, .sense-panel'
// A focusable item on either surface: a React Flow node on the flat plan, or a solid or zone in the isometric scene.
const ITEM = '.react-flow__node, [data-scene-item]'
// In the scene the arrow keys move an item along the screen. Its ground axes run diagonally, so right is +x and -y, down is +x and +y.
const SCREEN_NUDGE = { ArrowLeft: [-1, 1], ArrowRight: [1, -1], ArrowUp: [-1, -1], ArrowDown: [1, 1] }
const mod = (event) => event.metaKey || event.ctrlKey

/**
 * Every editing action on the canvas has a key. The handler runs before React Flow's own, so a nudge moves the
 * whole selection by the same step instead of one node by five pixels.
 */
export function useEditorKeys({ editing, ids, actions, onScreen = false }) {
  useEffect(() => {
    if (!editing) return undefined
    const onKey = (event) => {
      const target = event.target
      if (!(target instanceof Element) || target.closest(OWN_KEYS)) return
      const used = () => { event.preventDefault(); event.stopPropagation() }
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key
      if (mod(event) && key === 'z') { used(); (event.shiftKey ? actions.redo : actions.undo)(); return }
      if (mod(event) && key === 'y') { used(); actions.redo(); return }
      if (mod(event) && key === 'a') { used(); actions.selectAll(); return }
      if (mod(event) && key === 'g') { used(); actions.group(); return }
      if (event.altKey || event.metaKey || event.ctrlKey) {
        const grow = NUDGE[event.key]
        if (event.altKey && grow && ids.length === 1) { used(); actions.resize(grow[0] * STEP, grow[1] * STEP) }
        return
      }
      const node = target.closest(ITEM)
      const step = (onScreen ? SCREEN_NUDGE : NUDGE)[event.key]
      if (step) {
        if (!node || !ids.includes(node.getAttribute('data-id'))) return
        used()
        const size = event.shiftKey ? BIG_STEP : STEP
        actions.nudge(step[0] * size, step[1] * size)
        return
      }
      if (event.shiftKey && (event.key === 'Enter' || event.key === ' ') && node) { used(); actions.toggle(node.getAttribute('data-id')); return }
      if (event.key === 'F2' && ids.length) { used(); actions.settings(); return }
      if (target.closest('button, a')) return
      if ((event.key === 'Delete' || event.key === 'Backspace') && ids.length) { used(); actions.remove(); return }
      if (key === 'c' && ids.length === 1) { used(); actions.connect() }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [editing, ids, actions, onScreen])
}

/** The question mark opens the list, wherever focus is outside a field. */
export function useHelpKey(onHelp) {
  useEffect(() => {
    const onKey = (event) => {
      const target = event.target
      if (event.key !== '?' || (target instanceof Element && target.closest(OWN_KEYS))) return
      event.preventDefault()
      onHelp()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onHelp])
}

const GROUPS = [
  ['Build', [
    ['Drag a library type onto the canvas', 'Places it where you drop it, inside the container under it'],
    ['Enter on a library type', 'Places it on the canvas (inside the selected container, if one is selected)'],
    ['Drag from a dot on a node to another node', 'Draws a connection and asks which kind'],
    ['C', 'Draw a connection from the selected node'],
    ['Ctrl+G or Cmd+G', 'Group the selected items in a new container'],
  ]],
  ['Select', [
    ['Click', 'Select one item or connection'],
    ['Shift+click, Ctrl+click or Cmd+click', 'Add or remove an item'],
    ['Shift+drag on the canvas', 'Select everything in the box'],
    ['Shift+Enter on a focused node', 'Add or remove it from the selection'],
    ['Ctrl+A or Cmd+A', 'Select every item'],
    ['Escape', 'Clear the selection'],
  ]],
  ['Move and resize', [
    ['Arrow keys on a focused node', 'Move the selection 16 px (hold Shift for 64 px). In the scene they follow the screen'],
    ['Alt+Arrow keys', 'Make the selected container wider, narrower, taller or shorter by 16 px'],
    ['Drag the corners of a container', 'Resize it; it never shrinks past what it holds'],
    ['Inside, in the side panel', 'Choose which container an item sits in'],
  ]],
  ['Change and undo', [
    ['Ctrl+Z or Cmd+Z', 'Undo the last change'],
    ['Ctrl+Shift+Z, Ctrl+Y or Cmd+Shift+Z', 'Redo'],
    ['Delete or Backspace', 'Remove the selection (a container keeps what it held)'],
    ['F2', 'Go to the name field in the side panel'],
    ['?', 'Show this list'],
  ]],
]

export function ShortcutsDialog({ onClose, canEdit }) {
  return (
    <Modal title="Keyboard and mouse" onClose={onClose} wide footer={<button className="btn primary" onClick={onClose}>Close</button>}>
      {!canEdit && <p className="muted">You can select and look around. The editing keys work for people who can edit this clone.</p>}
      <div className="shortcuts">
        {GROUPS.map(([title, rows]) => (
          <section key={title}>
            <h4 className="inspector-tier">{title}</h4>
            <dl className="shortcut-list">
              {rows.map(([keys, what]) => <div key={keys}><dt>{keys}</dt><dd>{what}</dd></div>)}
            </dl>
          </section>
        ))}
      </div>
    </Modal>
  )
}
