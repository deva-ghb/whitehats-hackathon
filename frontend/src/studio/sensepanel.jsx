// "Where to watch": the sense points of the open clone, in both views. It runs the analysis, shows the coverage it gives, lists the
// suggestions with their reasons in plain words, and lets a person accept, reject or place one by hand. The same stored data is
// drawn in the scene as target rings with the robot; this list is how the flat plan shows it too.
import { useState } from 'react'
import { Icon } from '../components/ui'

const PANEL_KEY = 'wh-sense-panel'
const STATUS_WORDS = { accepted: 'Accepted', suggested: 'Suggested', rejected: 'Rejected' }
const percent = (share) => `${Math.round(share * 100)}%`

// Closed until a person opens it: the list is long and the canvas is what they came to look at. Their choice is remembered.
function remembered() {
  try { return window.localStorage.getItem(PANEL_KEY) === 'open' } catch { return false }
}

export function SensePanel({ sense, editing, selectedNode, onPick, shownVersion }) {
  const [kept, setKept] = useState(remembered)
  const open = kept
  const toggle = () => {
    setKept(!open)
    try { window.localStorage.setItem(PANEL_KEY, open ? 'closed' : 'open') } catch { /* the choice lasts for this visit */ }
  }
  const { data, points, busy, error } = sense
  const coverage = data?.coverage
  const hasPaths = Boolean(coverage?.paths_total)
  const stale = Boolean(data && shownVersion && data.has_run && data.version < shownVersion)
  const mine = selectedNode ? points.find((point) => point.node === selectedNode.id) : null

  return (
    <section className={`sense-panel ${open ? 'open' : ''}`} aria-label="Where to watch">
      <button className="sense-head" aria-expanded={open} aria-controls="sense-body" onClick={toggle}>
        <span className="sense-title"><Icon.eye size={16} />Where to watch</span>
        {coverage && <span className="sense-figure" data-testid="sense-coverage">{hasPaths ? percent(coverage.share) : 'No paths'}</span>}
        <Icon.chevron size={14} />
      </button>
      {open && (
        <div id="sense-body" className="sense-body">
          {!data && !error && <p className="muted">Reading the sense points.</p>}
          {error && <p className="field-problem" role="alert">{error}</p>}
          {data && (
            <>
              <p className="sense-lede" data-testid="sense-coverage-words">
                {hasPaths
                  ? <><b>{coverage.paths_watched}</b> of <b>{coverage.paths_total}</b> attack paths have an accepted sense point on them.</>
                  : 'No attack path leads to a crown jewel yet.'}
                {hasPaths && !coverage.is_complete && ' The search stopped early, so this is a lower bound.'}
              </p>
              <p className="sense-counts faint" data-testid="sense-counts">
                {data.counts.accepted} accepted, {data.counts.suggested} suggested, {data.counts.rejected} rejected
              </p>
              {stale && (
                <p className="sense-stale" role="status">
                  Worked out on version {data.version}; this clone is now at version {shownVersion}. Run it again to catch up: your decisions are kept.
                </p>
              )}
              {!data.has_run && !hasPaths && (
                <p className="muted">
                  Attack paths run from an entry point (a public edge, a repository, a pipeline) to a crown jewel. Mark what matters most with
                  Criticality set to Crown jewel on a node, then find where to watch.
                </p>
              )}
              {editing && (
                <div className="sense-actions">
                  <button className="btn small primary" disabled={Boolean(busy)} onClick={sense.analyse}>
                    {data.has_run ? 'Run again' : 'Find where to watch'}
                  </button>
                  {selectedNode && !mine && (
                    <button className="btn small" disabled={Boolean(busy)} onClick={() => sense.place(selectedNode.id)}>
                      Place a sense point on {selectedNode.name}
                    </button>
                  )}
                  {mine && mine.origin === 'manual' && (
                    <button className="btn small" disabled={Boolean(busy)} onClick={() => sense.remove(mine.id)}>Remove from {selectedNode.name}</button>
                  )}
                </div>
              )}
              {data.has_run && hasPaths && data.counts.suggested + data.counts.accepted + data.counts.rejected === 0 && (
                <p className="muted">Every path already has a node that is watched, so there is nothing to suggest.</p>
              )}
              <ol className="sense-list" aria-label="Sense points">
                {points.map((point) => (
                  <li key={point.id} className={`sense-item ${point.status}`} data-status={point.status} data-node={point.node}>
                    <div className="sense-line">
                      <button className="sense-name" onClick={() => onPick?.(point.node)} title="Show it on the canvas">
                        {point.rank > 0 && <span className="sense-rank">{point.rank}</span>}{point.node_name}
                      </button>
                      <span className={`sense-status ${point.status}`}>{point.origin === 'manual' ? 'Placed by hand' : STATUS_WORDS[point.status]}</span>
                    </div>
                    <p className="sense-reason" title={point.reason}><span className="sense-mode">{point.mode}</span> {point.reason}</p>
                    {editing && point.origin !== 'manual' && (
                      <div className="sense-decide">
                        {point.status !== 'accepted' && (
                          <button className="btn small" disabled={Boolean(busy)} aria-label={`Accept the sense point on ${point.node_name}`}
                            onClick={() => sense.decide(point.id, 'accepted')}>Accept</button>
                        )}
                        {point.status !== 'rejected' && (
                          <button className="btn small" disabled={Boolean(busy)} aria-label={`Reject the sense point on ${point.node_name}`}
                            onClick={() => sense.decide(point.id, 'rejected')}>Reject</button>
                        )}
                        {point.status !== 'suggested' && (
                          <button className="btn small" disabled={Boolean(busy)} aria-label={`Put the sense point on ${point.node_name} back to suggested`}
                            onClick={() => sense.decide(point.id, 'suggested')}>Undo</button>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
              <p className="sense-note faint">Reasons come from the clone and the node library. No model is needed.</p>
            </>
          )}
        </div>
      )}
    </section>
  )
}
