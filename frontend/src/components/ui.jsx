import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import * as SelectPrimitive from '@radix-ui/react-select'
import { copyText } from '../lib/api'
import Explain from './Explain'

/* ---------------- icons: one stroke set ---------------- */
const P = ({ children, size = 18 }) => (
  <svg className="ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)
export const Icon = {
  home: (p) => <P {...p}><path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /></P>,
  chat: (p) => <P {...p}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.8A8 8 0 1 1 21 12z" /></P>,
  check: (p) => <P {...p}><path d="M9 12l2 2 4-4" /><path d="M12 3l8 3v6c0 5-3.4 8-8 9-4.6-1-8-4-8-9V6z" /></P>,
  alert: (p) => <P {...p}><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></P>,
  wrench: (p) => <P {...p}><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z" /></P>,
  people: (p) => <P {...p}><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6" /></P>,
  pod: (p) => <P {...p}><path d="M12 2l8.5 5v10L12 22l-8.5-5V7z" /><path d="M12 22V12M20.5 7L12 12 3.5 7" /></P>,
  logs: (p) => <P {...p}><path d="M4 6h16M4 12h10M4 18h13" /></P>,
  network: (p) => <P {...p}><circle cx="12" cy="5" r="2.5" /><circle cx="5" cy="19" r="2.5" /><circle cx="19" cy="19" r="2.5" /><path d="M12 7.5v4M12 11.5L6.5 17M12 11.5l5.5 5.5" /></P>,
  swarm: (p) => <P {...p}><circle cx="12" cy="12" r="3" /><circle cx="5" cy="5" r="2" /><circle cx="19" cy="5" r="2" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="19" r="2" /><path d="M6.5 6.5L10 10M17.5 6.5L14 10M6.5 17.5L10 14M17.5 17.5L14 14" /></P>,
  brain: (p) => <P {...p}><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a2 2 0 0 0-3-1z" /><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1" /></P>,
  plug: (p) => <P {...p}><path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 0 1-10 0z" /><path d="M12 16v6" /></P>,
  report: (p) => <P {...p}><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></P>,
  settings: (p) => <P {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></P>,
  plus: (p) => <P {...p}><path d="M12 5v14M5 12h14" /></P>,
  arrow: (p) => <P {...p}><path d="M5 12h14M13 6l6 6-6 6" /></P>,
  tick: (p) => <P {...p}><path d="M5 12l5 5L20 7" /></P>,
  chevron: (p) => <P {...p}><path d="M6 9l6 6 6-6" /></P>,
  refresh: (p) => <P {...p}><path d="M21 12a9 9 0 0 1-15.4 6.4L3 16M3 12a9 9 0 0 1 15.4-6.4L21 8" /><path d="M21 3v5h-5M3 21v-5h5" /></P>,
  flag: (p) => <P {...p}><path d="M5 21V4M5 4h11l-2 4 2 4H5" /></P>,
  book: (p) => <P {...p}><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 19V5M9 7h6" /></P>,
  building: (p) => <P {...p}><path d="M4 21V5l8-3v19M12 9h8v12M8 8h.01M8 12h.01M8 16h.01M16 13h.01M16 17h.01" /></P>,
  spark: (p) => <P {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></P>,
  boundary: (p) => <P {...p}><rect x="3" y="4" width="18" height="16" rx="2" strokeDasharray="3 2.5" /></P>,
  cpu: (p) => <P {...p}><rect x="6" y="6" width="12" height="12" rx="1.5" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></P>,
  layers: (p) => <P {...p}><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></P>,
  code: (p) => <P {...p}><path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16" /></P>,
  queue: (p) => <P {...p}><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M8 6v12M13 6v12" /></P>,
  database: (p) => <P {...p}><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" /></P>,
  key: (p) => <P {...p}><circle cx="8" cy="15" r="4" /><path d="M11 12l9-9M16 7l3 3M14 9l2 2" /></P>,
  eye: (p) => <P {...p}><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></P>,
  cloud: (p) => <P {...p}><path d="M7 18a5 5 0 0 1-.6-10A6 6 0 0 1 18 9a4.5 4.5 0 0 1-.5 9z" /></P>,
  note: (p) => <P {...p}><path d="M5 3h10l4 4v14H5z" /><path d="M15 3v4h4M8 12h8M8 16h5" /></P>,
  search: (p) => <P {...p}><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></P>,
  fit: (p) => <P {...p}><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></P>,
  download: (p) => <P {...p}><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></P>,
  upload: (p) => <P {...p}><path d="M12 20V9M7 14l5-5 5 5M5 4h14" /></P>,
}

export const Logo = () => <img src="/favicon.svg" width="34" height="34" alt="" className="product-logo" />

const portraitIndex = {
  'ceo@acme.example': 0,
  'engineer@acme.example': 1,
  'ciso@acme.example': 2,
  'analyst@acme.example': 3,
  'health.engineer@acme.example': 4,
  'auditor@acme.example': 5,
  'approver@acme.example': 6,
  'operator@whitehats.example': 7,
}

export function Avatar({ name = '', email }) {
  const words = name.trim().split(' ').filter(Boolean)
  const initials = [words[0], words.length > 1 ? words.at(-1) : ''].map(word => word?.[0] || '').join('').toUpperCase()
  const tone = (initials.codePointAt(0) || 0) % 4
  const portrait = portraitIndex[email]
  return <span className="person-avatar" data-tone={tone} aria-hidden="true">
    {initials || '?'}
    {portrait !== undefined && <img alt="" src="/people/acme-portraits.jpg"
      style={{ left: `${-100 * (portrait % 4)}%`, top: `${-100 * Math.floor(portrait / 4)}%` }}
      onError={event => { event.currentTarget.hidden = true }} />}
  </span>
}
/* ---------------- primitives ---------------- */
const SEV = { critical: 'critical', high: 'high', medium: 'medium', low: 'low' }
export const Sev = ({ s }) => <span className={`pill ${SEV[s] || 'neutral'}`}>{s}</span>
export const SampleTag = ({ origin }) => (origin === 'sample' ? <Explain term="sample"><span className="pill neutral">Sample</span></Explain> : null)

const STATUS_CLS = {
  open: 'crit', investigating: 'warn', verifying: 'info', resolved: 'good', false_positive: 'neutral', pending: 'warn', dispatched: 'good',
  rejected: 'neutral', done: 'good', online: 'good', crashed: 'crit', running: 'good', late: 'warn', offline: 'crit', stopped: 'neutral',
  ready: 'good', needs_key: 'warn', error: 'crit', untested: 'neutral', active: 'good', learning: 'info', dark: 'crit',
  verified: 'good', configured: 'info', available: 'neutral', checked: 'info', reading: 'good', unbuilt: 'neutral', coming_soon: 'neutral',
  draft: 'neutral', onboarding: 'info', live: 'good', suspended: 'warn',
  offboarded: 'neutral', deleted: 'neutral', passed: 'good', failed: 'crit', waived: 'neutral', on_track: 'info', amber: 'warn', red: 'crit',
  accepted: 'neutral', revoked: 'neutral', stopped_reading: 'crit', check_failed: 'crit', skipped: 'neutral', cancelling: 'warn', cancelled: 'neutral', queued: 'neutral',
}
const STATUS_LABEL = {
  needs_key: 'needs key', dispatched: 'approved', verifying: 'WhiteHats is checking', false_positive: 'false positive',
  accepted: 'risk accepted', on_track: 'on track', done: 'done', amber: 'slipping', red: 'overdue',
  available: 'Not connected', checked: 'Credentials checked, not reading yet', reading: 'Reading data',
  unbuilt: 'Not built yet', coming_soon: 'Coming soon', configured: 'Credentials checked, not reading yet',
  stopped_reading: 'Stopped reading', check_failed: 'Check failed', skipped: 'Skipped', cancelling: 'Cancelling', cancelled: 'Cancelled',
}
export const Status = ({ s, wrap = false }) => (
  <span className={`pill ${wrap ? 'wrap' : ''} ${STATUS_CLS[s] || 'neutral'}`}>{STATUS_LABEL[s] || s}</span>
)

export function Card({ title, hint, action, children, pad = false, className = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <div className="card-head">
          <div>{title && <h2>{title}</h2>}{hint && <div className="hint">{hint}</div>}</div>
          {action}
        </div>
      )}
      <div className={pad ? 'card-body' : ''}>{children}</div>
    </section>
  )
}

export const Stat = ({ label, value, unit, note, tone }) => (
  <div className="card stat">
    <div className="label">{label}</div>
    <div className="value" style={tone ? { color: `var(--${tone})` } : undefined}>{value}{unit && <small>{unit}</small>}</div>
    {note && <div className="note">{note}</div>}
  </div>
)

export const Empty = ({ title, children, action }) => (
  <div className="empty"><h3>{title}</h3><div>{children}</div>{action && <div style={{ marginTop: 14 }}>{action}</div>}</div>
)

export const Loading = ({ rows = 3 }) => (
  <div className="stack" style={{ padding: 20 }}>
    {Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton" style={{ height: 18, width: `${90 - i * 12}%` }} />)}
  </div>
)

export const ErrorNote = ({ error, retry }) => (
  <div className="banner crit" role="alert">
    <div style={{ flex: 1 }}><b>Could not load this.</b> {error?.message}</div>
    {retry && <button className="btn small" onClick={retry}>Try again</button>}
  </div>
)

export function Modal({ title, onClose, children, footer, wide }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && !e.defaultPrevented && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-back" onMouseDown={onClose}>
      <div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head"><h3>{title}</h3><button className="x" onClick={onClose} aria-label="Close">×</button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

/** Previous and next through a long list, saying where in it the reader is. Renders nothing for one page. */
export function Pager({ page, size, total, onPage, label = 'items' }) {
  const pages = Math.max(1, Math.ceil(total / size))
  if (pages <= 1) return null
  const first = (page - 1) * size + 1
  const last = Math.min(total, page * size)
  return (
    <nav className="pager" aria-label={`Pages of ${label}`}>
      <span className="muted">Showing {first}–{last} of {total} {label}</span>
      <div className="row">
        <button type="button" className="btn small" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <span className="pager-at" aria-current="page">Page {page} of {pages}</span>
        <button type="button" className="btn small" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </div>
    </nav>
  )
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? 'on' : ''} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  )
}

export function Disclosure({ title, children, open }) {
  return <details className="detail-disclosure" open={open}>
    <summary>{title}</summary>
    <div className="detail-disclosure-body">{children}</div>
  </details>
}

export const Field = ({ label, help, children }) => {
  const id = !Array.isArray(children) && children?.props?.id
  return (
    <div className="field"><label htmlFor={id || undefined}>{label}</label>{children}{help && <div className="help">{help}</div>}</div>
  )
}

export function Select({ id, label, value, onChange, options, disabled, placeholder }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const typed = useRef({ text: '', timer: 0 })
  const selected = options.find((opt) => String(opt.value) === String(value))
  const searchable = options.length > 7
  const needle = query.trim().toLowerCase()
  const visible = options.filter((opt) => {
    if (!needle || String(opt.value) === String(value)) return true
    return `${opt.label} ${opt.description || ''} ${opt.group || ''}`.toLowerCase().includes(needle)
  })
  const loose = []
  const groups = []
  for (const opt of visible) {
    if (!opt.group) {
      loose.push(opt)
      continue
    }
    let bucket = groups.find((group) => group.label === opt.group)
    if (!bucket) {
      bucket = { label: opt.group, options: [] }
      groups.push(bucket)
    }
    bucket.options.push(opt)
  }
  const pick = useCallback((next) => {
    const opt = options.find((item) => String(item.value) === next)
    onChange(opt ? opt.value : next)
  }, [onChange, options])
  useEffect(() => {
    if (!open) return undefined
    const frame = requestAnimationFrame(() => {
      const content = document.querySelector('.select-content[data-state="open"]')
      if (!content) return
      const target = searchable
        ? content.querySelector('.select-search')
        : content.querySelector('[role="option"][data-state="checked"]') || content.querySelector('[role="option"]')
      target?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [open, searchable])
  const item = (opt) => {
    const Glyph = opt.icon && Icon[opt.icon]
    const note = opt.disabled ? opt.disabledReason : opt.description
    return (
      <SelectPrimitive.Item key={String(opt.value)} value={String(opt.value)} disabled={!!opt.disabled} className="select-option"
        onKeyDown={(event) => { if (event.key === ' ') event.preventDefault() }}>
        {Glyph && <Glyph size={16} />}
        <span className="select-copy">
          <SelectPrimitive.ItemText>{opt.label}</SelectPrimitive.ItemText>
          {note && <span className="select-desc">{note}</span>}
        </span>
        <SelectPrimitive.ItemIndicator><Icon.tick size={16} /></SelectPrimitive.ItemIndicator>
      </SelectPrimitive.Item>
    )
  }
  const TriggerGlyph = selected?.icon && Icon[selected.icon]
  return (
    <div className="select">
      {label && <label className="select-label" htmlFor={id}>{label}</label>}
      <SelectPrimitive.Root
        // Always controlled: with undefined Radix keeps its own choice, which then shows on the next item and is never saved.
        value={value == null ? '' : String(value)}
        onValueChange={pick}
        disabled={disabled}
        onOpenChange={(next) => { setOpen(next); if (!next) setQuery('') }}
      >
        <SelectPrimitive.Trigger id={id} className="input select-trigger">
          <span className="select-value">{TriggerGlyph && <TriggerGlyph size={16} />}<SelectPrimitive.Value placeholder={placeholder} /></span>
          <SelectPrimitive.Icon><Icon.chevron size={16} /></SelectPrimitive.Icon>
        </SelectPrimitive.Trigger>
        <SelectPrimitive.Portal>
          <SelectPrimitive.Content
            className={`select-content${open ? ' open' : ''}`}
            position="popper"
            side="bottom"
            sideOffset={6}
            onKeyDown={(event) => {
              const options = [...event.currentTarget.querySelectorAll('[role="option"]:not([data-disabled])')]
              if (!options.length) return
              const current = options.findIndex((opt) => opt === document.activeElement || opt.contains(document.activeElement))
              const labelOf = (opt) => (opt.querySelector('.select-copy')?.firstChild?.textContent || '').trim().toLowerCase()
              if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                const next = event.key === 'Home' ? 0
                  : event.key === 'End' ? options.length - 1
                    : event.key === 'ArrowDown' ? Math.min(options.length - 1, (current < 0 ? -1 : current) + 1)
                      : Math.max(0, (current < 0 ? options.length : current) - 1)
                options[next]?.focus()
                event.preventDefault()
                event.stopPropagation()
                return
              }
              if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) return
              window.clearTimeout(typed.current.timer)
              typed.current.text = `${typed.current.text}${event.key}`.toLowerCase()
              typed.current.timer = window.setTimeout(() => { typed.current.text = '' }, 1000)
              const q = typed.current.text
              const start = q.length === 1 ? current + 1 : Math.max(current, 0)
              const ordered = [...options.slice(start), ...options.slice(0, start)]
              const match = ordered.find((opt) => labelOf(opt).startsWith(q))
              if (!match) return
              match.focus()
              event.preventDefault()
              event.stopPropagation()
            }}
          >
            <div className="select-pop">
              {searchable && (
                <input
                  className="input select-search"
                  aria-label="Search options"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (['ArrowUp', 'ArrowDown', 'Home', 'End', 'Enter', 'Escape', 'Tab'].includes(event.key)) {
                      if (event.key !== 'Tab') event.preventDefault()
                      return
                    }
                    event.stopPropagation()
                  }}
                />
              )}
              <SelectPrimitive.Viewport className="select-viewport">
                {loose.map(item)}
                {groups.map((group) => (
                  <SelectPrimitive.Group key={group.label}>
                    <SelectPrimitive.Label className="select-group">{group.label}</SelectPrimitive.Label>
                    {group.options.map(item)}
                  </SelectPrimitive.Group>
                ))}
                {visible.length === 0 && <div className="select-empty">No matches</div>}
              </SelectPrimitive.Viewport>
            </div>
          </SelectPrimitive.Content>
        </SelectPrimitive.Portal>
      </SelectPrimitive.Root>
    </div>
  )
}

export const Bar = ({ value, tone }) => (
  <div className={`bar ${tone || ''}`}><i style={{ width: `${Math.max(0, Math.min(100, value || 0))}%` }} /></div>
)

export function CodeBlock({ title, text }) {
  const toast = useToast()
  return (
    <div>
      <div className="copy-row">
        <b style={{ fontSize: 13 }}>{title}</b>
        <button className="btn small" onClick={async () => toast((await copyText(text)) ? 'Copied' : 'Select the text and copy it')}>Copy</button>
      </div>
      <pre className="code">{text}</pre>
    </div>
  )
}

/* ---------------- charts ---------------- */
export function Gauge({ score, grade }) {
  const r = 70, c = Math.PI * r, frac = Math.max(0, Math.min(1, score / 1000))
  const tone = score >= 800 ? 'var(--good)' : score >= 650 ? 'var(--brand)' : score >= 450 ? 'var(--warn)' : 'var(--crit)'
  return (
    <svg viewBox="0 0 180 110" width="200" role="img" aria-label={`Posture ${score} of 1000, ${grade}`}>
      <path d="M20 95 A70 70 0 0 1 160 95" fill="none" stroke="#edf0f3" strokeWidth="14" strokeLinecap="round" />
      <path d="M20 95 A70 70 0 0 1 160 95" fill="none" stroke={tone} strokeWidth="14" strokeLinecap="round"
        strokeDasharray={`${c * frac} ${c}`} />
      <text x="90" y="82" textAnchor="middle" fontSize="34" fontWeight="800" fill="var(--ink)" fontFamily="var(--f-ui)">{score}</text>
      <text x="90" y="102" textAnchor="middle" fontSize="11" fill="var(--muted)" fontFamily="var(--f-ui)">of 1000</text>
    </svg>
  )
}

function dayIndex(at) {
  const [y, m, d] = String(at).slice(0, 10).split('-').map(Number)
  return Date.UTC(y, m - 1, d) / 86400000
}

/** A missing day is a gap, not a straight line through measurements that were never taken. */
export function chartHint(rows, started, formatDay) {
  if (!rows?.length) return 'No reading yet'
  const gap = rows.some((row, i) => i > 0 && dayIndex(row.day) - dayIndex(rows[i - 1].day) > 1)
  if (gap || rows.length < 30) {
    const day = started || rows[0].day
    return <>History starts <time dateTime={day}>{formatDay(day)}</time>. Only days with a reading are shown.</>
  }
  return 'Higher is safer'
}

export function LineChart({ points, height = 160, width = 460, min, max, label = 'value' }) {
  if (!points?.length) return <Empty title="No history yet">A reading appears after the daily snapshot.</Empty>
  const W = width, H = height, pad = { l: 34, r: 10, t: 12, b: 22 }
  const vals = points.map((p) => p.value)
  const lo = min ?? Math.floor((Math.min(...vals) - 20) / 50) * 50
  const hi = max ?? Math.ceil((Math.max(...vals) + 20) / 50) * 50
  const dated = points.every((p) => p.at)
  const indexes = dated ? points.map((p) => dayIndex(p.at)) : points.map((_, i) => i)
  const span = Math.max(1, indexes[indexes.length - 1] - indexes[0])
  const x = (index) => pad.l + ((index - indexes[0]) / span) * (W - pad.l - pad.r)
  const y = (v) => pad.t + (1 - (v - lo) / Math.max(1, hi - lo)) * (H - pad.t - pad.b)
  const segments = []
  let gaps = 0
  for (let i = 1; i < points.length; i++) {
    const step = indexes[i] - indexes[i - 1]
    if (dated && step !== 1) {
      gaps += 1
      continue
    }
    segments.push([indexes[i - 1], points[i - 1].value, indexes[i], points[i].value])
  }
  const ticks = [lo, (lo + hi) / 2, hi]
  const last = points[points.length - 1]
  const gapLabel = gaps ? `, ${gaps} ${gaps === 1 ? 'gap' : 'gaps'}` : ''
  return (
    <svg className="line-chart" data-gaps={gaps} viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
      aria-label={`${label} on ${points.length} ${points.length === 1 ? 'day' : 'days'}${gapLabel}, latest ${last.value}`}>
      {ticks.map((t) => (
        <g key={t}><line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="#edf0f3" />
          <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">{Math.round(t)}</text></g>
      ))}
      {gaps === 0 && points.length > 1 && (
        <path d={`${points.map((p, i) => `${i ? 'L' : 'M'}${x(indexes[i]).toFixed(1)} ${y(p.value).toFixed(1)}`).join(' ')} L${x(indexes[indexes.length - 1])} ${H - pad.b} L${x(indexes[0])} ${H - pad.b} Z`} fill="var(--brand-soft)" opacity=".7" />
      )}
      {segments.map((seg, i) => (
        <path key={i} d={`M${x(seg[0]).toFixed(1)} ${y(seg[1]).toFixed(1)} L${x(seg[2]).toFixed(1)} ${y(seg[3]).toFixed(1)}`} fill="none" stroke="var(--brand)" strokeWidth="2.2" strokeLinejoin="round" />
      ))}
      {points.map((p, i) => (
        <circle key={p.at || i} cx={x(indexes[i])} cy={y(p.value)} r="4.5" fill="var(--brand)" stroke="#fff" strokeWidth="2" />
      ))}
      <text x={pad.l} y={H - 5} fontSize="11" fill="var(--muted)">{points[0].label}</text>
      {points.length > 1 && <text x={W - pad.r} y={H - 5} fontSize="11" fill="var(--muted)" textAnchor="end">{last.label}</text>}
    </svg>
  )
}

/* ---------------- toasts ---------------- */
const ToastCtx = createContext(() => {})
export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const push = useCallback((text, kind = 'info', action = null) => {
    const id = Math.random()
    setItems((s) => [...s, { id, text, kind, action }])
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), action?.ms || 3800)
  }, [])
  const dismiss = (id) => setItems((s) => s.filter((t) => t.id !== id))
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">{items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <span>{t.text}</span>
          {t.action && <button className="btn small" onClick={() => { dismiss(t.id); t.action.onClick() }}>{t.action.label}</button>}
        </div>
      ))}</div>
    </ToastCtx.Provider>
  )
}
export const useToast = () => useContext(ToastCtx)

// Run an async action with busy state and a toast for success or failure.
export function useAction() {
  const toast = useToast()
  const [busy, setBusy] = useState(null)
  const run = useCallback(async (key, fn, ok) => {
    setBusy(key)
    try {
      const out = await fn()
      if (ok) toast(typeof ok === 'function' ? ok(out) : ok)
      return out
    } catch (e) {
      toast(e.message, 'error')
      return undefined
    } finally {
      setBusy(null)
    }
  }, [toast])
  return [busy, run]
}
