import { useState } from 'react'
import { Link } from 'react-router-dom'
import { fmt } from '../lib/api'
import { Icon } from '../components/ui'

// "arn:aws:iam::1:user/wh-demo" -> "wh-demo": the resource's own name, as the activity history writes it.
const shortName = (resource) => String(resource || '').split('/').pop().split(':').pop()

/** Open cloud alerts about things the map does not draw: users, roles, the account itself. Without this, "full
 * administrator access was granted" would be on no building at all. What was there before WhiteHats first looked is
 * counted, not listed: it is backlog, not what just happened. */
export function AccountAlerts({ data }) {
  const live = data?.unplaced || []
  const backlog = data?.unplaced_backlog || 0
  const [open, setOpen] = useState(false) // a pill with its count until asked: it never covers the map unasked
  if (!live.length && !backlog) return null
  const shown = open
  return (
    <section className={`account-alerts ${shown ? 'open' : ''}`} aria-label="Also in this account">
      <button className="sense-head" aria-expanded={shown} aria-controls="account-alerts-body" onClick={() => setOpen(!shown)}>
        <span className="sense-title"><Icon.alert size={16} /><span className="corner-label">Also in this account</span></span>
        {live.length > 0 && <span className="account-alerts-count">{live.length}</span>}
      </button>
      {shown && (
        <div id="account-alerts-body" className="sense-body">
          <p className="faint">Not drawn on the map: users, roles and the account itself.</p>
          <ul className="sense-list">
            {live.map((a) => (
              <li key={a.id} className={`account-alert ${a.severity}`}>
                <strong>{a.title}</strong>
                <span className="faint">{[a.severity, shortName(a.resource), fmt.ago(a.at)].filter(Boolean).join(' · ')}</span>
                {a.incident_id ? <Link to={`/incidents/${a.incident_id}`}>Open its incident</Link> : <Link to="/">See it in Latest signals</Link>}
              </li>
            ))}
          </ul>
          {backlog > 0 && <p className="faint">{backlog} more {backlog === 1 ? 'was' : 'were'} already there when WhiteHats first looked: listed on the Overview to review.</p>}
        </div>
      )}
    </section>
  )
}
