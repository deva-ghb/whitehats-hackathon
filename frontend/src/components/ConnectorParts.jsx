// How a connection is shown, in words: its contract (each permission ok or missing, what it found) and its honest status.
// Shared by the Connectors screen and the connection node's panel on the canvas.
import { fmt } from '../lib/api'

/** The connector contract in words: each permission ok or missing, and what has been found. */
export function ContractStatus({ contract }) {
  const checked = contract.permissions.filter((p) => p.ok !== null)
  const missing = contract.permissions.filter((p) => p.ok === false)
  return (
    <div className="contract stack" style={{ gap: 6 }}>
      {contract.sign_in_error && !checked.length && <p className="contract-line bad">Could not sign in, so no permission was checked.</p>}
      {checked.length > 0 && <p className={`contract-line ${missing.length ? 'bad' : 'ok'}`} data-testid="permission-summary">
        {missing.length ? `${missing.length} of ${contract.permissions.length} permissions missing` : `All ${contract.permissions.length} permissions ok`}
        {contract.checked_at && <span className="faint"> · checked {fmt.ago(contract.checked_at)}</span>}</p>}
      {missing.length > 0 && <ul className="contract-missing" aria-label="Missing permissions">{missing.map((p) => (
        <li key={p.name}><code className="mono">{p.name}</code> is missing. WhiteHats needs it to {p.why}.</li>))}</ul>}
      {checked.length > 0 && <details className="contract-all"><summary>Every permission</summary>
        <ul>{contract.permissions.map((p) => <li key={p.name}><span className={`pill ${p.ok ? 'good' : p.ok === false ? 'crit' : 'neutral'}`}>{p.ok ? 'Ok' : p.ok === false ? 'Missing' : 'Not checked'}</span> <code className="mono">{p.name}</code></li>)}</ul></details>}
      {contract.discovered_at && <p className="faint" style={{ fontSize: 12 }}>Found {fmt.n(contract.discovered)} resources {fmt.ago(contract.discovered_at)}</p>}
    </div>
  )
}

export function lagText(contract) {
  if (!contract || contract.lag_seconds == null) return ''
  const minutes = Math.round(contract.lag_seconds / 60)
  return minutes < 1 ? ' · up to date' : ` · ${minutes} min behind`
}

/** Same rule as cloudlogs.can_read on the server: checked, reading, or failed after reading before. */
export function canRead(c) {
  if (c.category !== 'Cloud logs') return false
  return c.status === 'checked' || c.status === 'reading' || (c.status === 'error' && Boolean(c.last_sync_at))
}

/** A read that failed after reading before has stopped; a failed check alone has not. */
export function stoppedReading(c) {
  return c.status === 'error' && Boolean(c.last_sync_at) && Boolean(c.sync?.last_error)
}

/** The missing permissions already say what the check found, in words; the raw AWS message would repeat it. */
export function hasMissing(c) {
  return Boolean(c.contract?.permissions.some((p) => p.ok === false))
}

/** A stored check: permissions were probed, or sign-in failed before any probe. */
export function hasChecked(c) {
  const contract = c.contract
  if (!contract) return false
  return Boolean(contract.checked_at || contract.sign_in_error || contract.permissions.some((p) => p.ok !== null))
}

/** An empty contract would only add a gap. Show it once a check, a sign-in failure or a discovery has something to say. */
export function contractHasNews(contract) {
  if (!contract) return false
  if (contract.sign_in_error || contract.discovered_at) return true
  return contract.permissions.some((p) => p.ok !== null)
}

/** Where a connection stands, in a word or two, for a node's badge. A problem is told by `problem`, so it never relies on colour. */
export function standing(c) {
  const words = { available: 'Not connected', checked: 'Checked', configured: 'Checked', verified: 'Checked', reading: 'Reading',
    stopped_reading: 'Stopped reading', check_failed: 'Check failed', coming_soon: 'Coming soon' }
  const status = shownStatus(c)
  return { text: words[status] || status, problem: status === 'stopped_reading' || status === 'check_failed' || hasMissing(c) }
}

/** An error says which kind: stopped reading, or a check that failed. */
export function shownStatus(c) {
  if (c.status !== 'error') return c.status
  return stoppedReading(c) ? 'stopped_reading' : 'check_failed'
}
