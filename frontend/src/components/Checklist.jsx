import { Link } from 'react-router-dom'
import { fmt } from '../lib/api'
import { Card, SampleTag, Status } from './ui'

const MARK = { passed: '✓', failed: '!', waived: '–', pending: '' }

export function ReadinessBar({ readiness }) {
  const done = readiness.checks.filter((c) => ['passed', 'waived'].includes(c.status)).length
  return (
    <div>
      <div className="row between" style={{ fontSize: 12.5, marginBottom: 6 }}>
        <b>{done} of {readiness.total} checks complete</b>
        <span className={readiness.ready_for_signoff ? 'pill good' : 'pill warn'}>{readiness.ready_for_signoff ? 'Ready for sign-off' : `${readiness.blocking.length} blocking`}</span>
      </div>
      <div className="progress-line">{readiness.checks.map((c) => <i key={c.key} className={['passed', 'waived'].includes(c.status) ? 'on' : ''} title={c.title} />)}</div>
    </div>
  )
}

export default function Checklist({ readiness, actions, only }) {
  return (
    <Card>
      {readiness.stages.map((st) => {
        const items = readiness.checks.filter((c) => c.stage === st.key && (!only || only(c)))
        if (!items.length) return null
        const done = items.filter((c) => ['passed', 'waived'].includes(c.status)).length
        return (
          <div key={st.key} className="stage">
            <div className="stage-head"><span>{st.title}</span><span className="faint">{done}/{items.length}</span></div>
            {items.map((c) => (
              <div key={c.key} className="check">
                <span className={`mark ${c.status}`} aria-label={c.status}>{MARK[c.status]}</span>
                <div>
                  <div className="t">{c.title} <SampleTag origin={c.origin} />{!c.required && <span className="pill neutral" style={{ marginLeft: 8 }}>optional</span>}</div>
                  <div className="d">{c.detail || c.why}</div>
                  <div className="meta">
                    <span>{c.kind === 'auto' ? 'Checked automatically' : 'Ticked by WhiteHats'}</span>
                    <span>Owner: {c.owner === 'customer' ? 'customer' : 'WhiteHats'}</span>
                    {c.checked_at && <span>{c.checked_by === 'automatic' ? 'Last checked' : c.checked_by} · {fmt.time(c.checked_at)}</span>}
                    {c.doc && <Link to={`/help/${c.doc}`}>How to</Link>}
                  </div>
                </div>
                <div className="acts"><Status s={c.status} />{actions && actions(c)}</div>
              </div>
            ))}
          </div>
        )
      })}
    </Card>
  )
}

export function Milestones({ items }) {
  const origin = items.some((m) => m.origin === 'sample') ? 'sample' : 'live'
  return (
    <Card title={<span className="row wrap">Time to first value <SampleTag origin={origin} /></span>} hint="Target days from the day the company was created">
      <div className="milestones">
        {items.map((m) => (
          <div key={m.key} className="milestone">
            <span className={`dot ${m.state === 'done' ? 'good' : m.state === 'red' ? 'crit' : m.state === 'amber' ? 'warn' : 'neutral'}`} />
            <div><b style={{ fontSize: 13.5 }}>{m.title}</b><div className="faint" style={{ fontSize: 12 }}>Target: day {m.target_day}{m.done_at ? ` · done ${fmt.date(m.done_at)}` : ` · due ${fmt.date(m.due)}`}</div></div>
            <Status s={m.state} />
          </div>
        ))}
      </div>
    </Card>
  )
}
