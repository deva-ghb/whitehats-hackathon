import { useState } from 'react'
import { useApi } from '../lib/api'
import { Icon } from '../components/ui'

/** REC.1: what the customer could switch on in their own account so WhiteHats sees more. WhiteHats never switches
 * anything on itself: each item says why it matters, how to do it and what it costs, and the customer decides. A pill
 * with its count until asked, at the top right, so it never covers the map unasked. */
export function Recommendations() {
  const { data } = useApi('/api/recommendations', { poll: 60_000 })
  const [open, setOpen] = useState(false)
  const items = data?.items || []
  if (!items.length) return null
  return (
    <section className={`studio-recs ${open ? 'open' : ''}`} aria-label="Recommended">
      <button className="sense-head" aria-expanded={open} aria-controls="studio-recs-body" onClick={() => setOpen(!open)}>
        <span className="sense-title"><Icon.spark size={16} /><span className="corner-label">Recommended</span></span>
        <span className="studio-recs-count">{items.length}</span>
      </button>
      {open && (
        <div id="studio-recs-body" className="sense-body">
          <p className="faint">WhiteHats only reads your account. Switching these on in AWS lets it see more.</p>
          <ul className="sense-list">
            {items.map((r) => (
              <li key={r.id} className={`studio-rec ${r.severity}`}>
                <strong>{r.title}</strong>
                <span>{r.why}</span>
                <details>
                  <summary>How, and what it costs</summary>
                  <p>{r.how}</p>
                  <p className="faint">Cost: {r.cost}</p>
                </details>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
