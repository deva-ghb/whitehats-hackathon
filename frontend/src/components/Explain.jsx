import { cloneElement, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { GLOSSARY } from '../lib/glossary'

const HOLD_MS = 450 // a press this long on a button explains it instead of pressing it
const GAP = 8
const EDGE = 12

// GL.1: explains one platform word on the element it wraps. Opens on mouse hover and keyboard focus; on a phone a tap
// opens it on a label, and a press-and-hold on a button (a tap still presses the button). Screen readers read it as the
// element's description. `term` is a GLOSSARY key; `text` overrides it.
export default function Explain({ term, text, children }) {
  const words = text || GLOSSARY[term]
  const id = useId()
  const anchor = useRef(null)
  const [open, setOpen] = useState(false)
  const hold = useRef({ timer: 0, fired: false })
  const child = children
  const actionable = typeof child.type === 'string' ? ['button', 'a', 'input', 'select'].includes(child.type) : true

  useEffect(() => {
    if (!open) return undefined
    const close = (e) => { if (e.type !== 'keydown' || e.key === 'Escape') setOpen(false) }
    const outside = (e) => { if (!anchor.current?.contains(e.target)) setOpen(false) }
    window.addEventListener('keydown', close)
    window.addEventListener('scroll', close, true)
    document.addEventListener('pointerdown', outside)
    return () => {
      window.removeEventListener('keydown', close)
      window.removeEventListener('scroll', close, true)
      document.removeEventListener('pointerdown', outside)
    }
  }, [open])

  if (!words) return child
  const call = (name, e) => child.props[name]?.(e)
  const props = {
    ref: anchor,
    'aria-describedby': [child.props['aria-describedby'], id].filter(Boolean).join(' '),
    onPointerEnter: (e) => { call('onPointerEnter', e); if (e.pointerType === 'mouse') setOpen(true) },
    onPointerLeave: (e) => { call('onPointerLeave', e); if (e.pointerType === 'mouse') setOpen(false) },
    onFocus: (e) => { call('onFocus', e); if (e.target.matches(':focus-visible')) setOpen(true) },
    onBlur: (e) => { call('onBlur', e); setOpen(false) },
    onPointerDown: (e) => {
      call('onPointerDown', e)
      if (e.pointerType !== 'touch' || !actionable) return
      hold.current.fired = false
      hold.current.timer = window.setTimeout(() => { hold.current.fired = true; setOpen(true) }, HOLD_MS)
    },
    onPointerUp: (e) => { call('onPointerUp', e); window.clearTimeout(hold.current.timer) },
    onPointerCancel: (e) => { call('onPointerCancel', e); window.clearTimeout(hold.current.timer) },
    onContextMenu: (e) => { if (hold.current.fired) e.preventDefault(); call('onContextMenu', e) },
    onClickCapture: (e) => {
      if (hold.current.fired) { // the press explained the button; it does not also press it
        hold.current.fired = false
        e.preventDefault()
        e.stopPropagation()
        return
      }
      if (!actionable) setOpen((was) => !was)
      call('onClickCapture', e)
    },
  }
  if (!actionable) {
    props.tabIndex = child.props.tabIndex ?? 0
    props.className = `${child.props.className || ''} explained`.trim()
  }
  return (
    <>
      {cloneElement(child, props)}
      <Bubble id={id} open={open} anchor={anchor}>{words}</Bubble>
    </>
  )
}

// Always in the page (hidden while closed) so aria-describedby always has words to read.
function Bubble({ id, open, anchor, children }) {
  const self = useRef(null)
  const [place, setPlace] = useState(null)
  useLayoutEffect(() => {
    if (!open || !anchor.current || !self.current) return
    const a = anchor.current.getBoundingClientRect()
    const b = self.current.getBoundingClientRect()
    const below = a.bottom + GAP + b.height <= window.innerHeight - EDGE
    const left = Math.min(Math.max(EDGE, a.left + a.width / 2 - b.width / 2), window.innerWidth - b.width - EDGE)
    setPlace({ top: below ? a.bottom + GAP : a.top - GAP - b.height, left })
  }, [open, anchor])
  return createPortal(
    <span id={id} ref={self} role="tooltip" className="explain-bubble" data-open={open && place ? 'true' : 'false'}
      style={place && open ? { top: place.top, left: place.left } : undefined}>{children}</span>,
    document.body,
  )
}
