import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { allowed, useAuth } from '../App'
import { api, fmt, useApi } from '../lib/api'
import { Card, Empty, ErrorNote, Field, Gauge, Icon, LineChart, Loading, Modal, SampleTag, Select, Sev, Stat, Status, chartHint, useAction } from '../components/ui'
import Explain from '../components/Explain'
import FalsePositive from '../components/FalsePositive'

const KIND_LABEL = { fix: 'Fix to approve', risk: 'Urgent risk', factory: 'Proven fixes', blind: 'Blind spot' }
const SHOWN = 8

function SignalRow({ signal, onChange }) {
  return (
    <div className="list-item signal-row">
      <span className="signal-sev"><Sev s={signal.severity} /></span>
      <span className="signal-title">{signal.title}</span>
      <span className="signal-tag"><SampleTag origin={signal.origin} /></span>
      <span className="signal-source">{[signal.agent, fmt.ago(signal.at)].filter(Boolean).join(' · ')}</span>
      {signal.origin !== 'sample' && <span className="signal-act"><FalsePositive signal={signal} onDone={onChange} className="btn sm ghost" /></span>}
    </div>
  )
}

// What a pushed change shows (VS.4): each finding names its file and line, its branch and the commit, and says in
// words whether it is still open or a later commit closed it.
// SCAN.5: the five reasons OpenVEX allows for "not affected", in everyday words.
const NOT_AFFECTED_BECAUSE = [
  { value: 'vulnerable_code_not_in_execute_path', label: 'The weak code is never run' },
  { value: 'vulnerable_code_cannot_be_controlled_by_adversary', label: 'An attacker cannot reach or control it' },
  { value: 'inline_mitigations_already_exist', label: 'Something else already stops it' },
  { value: 'vulnerable_code_not_present', label: 'The weak code is not in this version' },
  { value: 'component_not_present', label: 'The part is not in this product' },
]

function CodeFinding({ f, canTriage, onMark, onWithdraw, busy, onChange }) {
  const closed = f.status === 'closed'
  const pill = closed ? ['good', `Closed: ${f.closed_reason}`] : f.vex ? ['neutral', 'Not affected'] : ['warn', 'Open']
  return (
    <div className="list-item code-finding" data-state={closed ? 'closed' : f.vex ? 'not-affected' : 'open'}>
      <div className="row wrap">
        <Sev s={f.severity} />
        <b className="code-finding-title">{f.title}</b>
        <span className={`pill code-finding-state ${pill[0]}`}>{pill[1]}</span>
      </div>
      <div className="muted code-finding-where">
        <code>{f.where}</code> on <code>{f.branch || 'the default branch'}</code> in {f.repo}
        {f.commit && <> · commit <a href={f.commit_url} target="_blank" rel="noreferrer">{f.commit}</a></>}
        {f.check_url && <> · <a href={f.check_url} target="_blank" rel="noreferrer">WhiteHats check on GitHub</a></>}
        {' '}· {fmt.ago(f.at)}
      </div>
      {f.explanation && <div className="code-finding-why">{f.explanation}</div>}
      {f.vex && <div className="code-finding-vex">{f.vex}</div>}
      {canTriage && !closed && (
        <div className="row code-finding-actions">
          {f.vex
            ? <button className="btn sm" disabled={busy} onClick={() => onWithdraw(f)}>Withdraw &ldquo;not affected&rdquo;</button>
            : !f.image_finding && <Explain term="not_affected"><button className="btn sm" onClick={() => onMark(f)}>Not affected&hellip;</button></Explain>}
          <FalsePositive signal={f} onDone={onChange} />
        </div>
      )}
      {canTriage && closed && (f.closed_reason === 'false positive' || f.closed_reason === 'risk accepted') && (
        <div className="row code-finding-actions"><Reopen signal={f} onDone={onChange} /></div>
      )}
    </div>
  )
}

function Reopen({ signal, onDone }) {
  const { user } = useAuth()
  const [busy, run] = useAction()
  if (!allowed(user, 'approve')) return null
  return <button type="button" className="btn sm" disabled={busy === 'reopen'} onClick={() => run('reopen', () => api(`/api/signals/${signal.id}/reopen`, { method: 'POST' }), 'Reopened: it counts again').then((out) => out && onDone?.())}>Reopen</button>
}

function NotAffected({ finding, busy, onClose, onSave }) {
  const [because, setBecause] = useState(NOT_AFFECTED_BECAUSE[0].value)
  const [reason, setReason] = useState('')
  const [until, setUntil] = useState('')
  return (
    <Modal title={`Not affected: ${finding.title}`} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy || !reason.trim()}
          onClick={() => onSave({ status: 'not_affected', justification: because, reason, until: until || null })}>Record it</button></>}>
      <p className="muted">The finding stays listed, marked not affected, with your reason and name. It is written as a VEX statement the scanners read, and it opens again if you withdraw it or it ends.</p>
      <Select id="vex-because" label="Why does it not apply?" value={because} onChange={setBecause} options={NOT_AFFECTED_BECAUSE} />
      <Field label="In your own words" help="One line. It is shown on the finding and kept in the project's VEX.">
        <textarea id="vex-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="For example: this endpoint is only reachable from the build server" />
      </Field>
      <Field label="Until (optional)" help="After this date the finding is open again, so the decision is looked at again.">
        <input id="vex-until" type="date" className="input" value={until} onChange={(e) => setUntil(e.target.value)} />
      </Field>
    </Modal>
  )
}

export default function Overview() {
  const nav = useNavigate()
  const { data, error, loading, reload } = useApi('/api/overview', { poll: 30000 })
  const brief = useApi('/api/overview/summary')
  // The briefing quotes the score, so when the score moves (a decision here, or the 30-second refresh) it is read again.
  const briefScore = useRef(null)
  const score = data?.posture?.score
  useEffect(() => {
    if (score === undefined) return
    if (briefScore.current !== null && briefScore.current !== score) brief.reload()
    briefScore.current = score
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [score])
  const [busy, run] = useAction()
  const { user } = useAuth()
  const canTriage = allowed(user, 'triage')
  const [marking, setMarking] = useState(null)
  const markNotAffected = (body) => run(`vex-${marking.id}`, () => api(`/api/signals/${marking.id}/vex`, { method: 'POST', body }),
    'Recorded as not affected').then((out) => { if (out) { setMarking(null); reload() } })
  const withdraw = (f) => run(`vex-${f.id}`, () => api(`/api/vex/${f.vex_statement_id}/withdraw`, { method: 'POST' }),
    'Withdrawn: the finding is open again').then((out) => out && reload())

  if (loading && !data) return <Loading rows={6} />
  if (error && !data) return <ErrorNote error={error} retry={reload} />
  const { posture, history, history_started, kpis, decisions, incidents, signals, earlier_signals: earlier = [], code_findings: codeFindings = [] } = data
  const points = history.map((h) => ({ value: h.score, label: fmt.date(h.day), at: h.day }))
  const historyHint = chartHint(history, history_started, fmt.date)
  const gradeTone = posture.grade === 'Unseen' ? 'warn' : posture.score >= 800 ? 'good' : posture.score >= 650 ? 'brand' : posture.score >= 450 ? 'warn' : 'crit'

  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card hero">
        <div className="hero-score">
          <div className="muted" style={{ fontWeight: 700, fontSize: 12.5 }}>Threat posture</div>
          <Gauge score={posture.score} grade={posture.grade} />
          <span className={`pill ${gradeTone}`}>{posture.grade}</span>
        </div>
        <div className="hero-body">
          <div className="row between">
            <b style={{ fontSize: 13, color: 'var(--muted)' }}>Your briefing</b>
            <button className="btn ghost small" disabled={busy === 'brief'} onClick={() => run('brief', async () => {
              brief.setData(await api('/api/overview/summary?refresh=true'))
            })}>{busy === 'brief' ? <span className="spin" /> : <><Icon.refresh size={14} /> Refresh</>}</button>
          </div>
          {brief.data ? <p className="brief">{brief.data.text}</p> : <div className="skeleton" style={{ height: 60 }} />}
          {brief.data && <div className="faint" style={{ fontSize: 12 }}>Written by {brief.data.model} from {fmt.n(brief.data.context_tokens)} tokens of context</div>}
          <div className="row wrap" style={{ gap: 8 }}>
            {posture.breakdown.filter((b) => b.penalty > 0).map((b) => (
              <span key={b.area} className="pill neutral wrap" title={b.detail}>{`-${b.penalty} points: ${b.area}`}</span>
            ))}
          </div>
        </div>
      </section>

      <div className="grid g4">
        <Stat label="Open incidents" value={kpis.open_incidents} note={`${kpis.critical_incidents} critical`} tone={kpis.critical_incidents ? 'crit' : undefined} />
        <Stat label="Collectors reporting" value={`${kpis.collectors_online}/${kpis.collectors_total}`} note={kpis.collectors_total === 0 ? 'none yet: nothing is being watched' : kpis.collectors_online < kpis.collectors_total ? 'some are silent: blind spots' : 'all reporting'} tone={kpis.collectors_total === 0 || kpis.collectors_online < kpis.collectors_total ? 'warn' : undefined} />
        <Stat label="Fixes approved" value={kpis.fixes_7d} note="in the last 7 days" />
        <Stat label="Signals" value={fmt.n(kpis.signals_7d)} note={`last 7 days · ${kpis.open_network_findings} network findings open`} />
      </div>

      <div className="split">
        <Card title="Decisions waiting for you" hint="Most urgent first" action={<Link to="/approvals" className="btn small">All approvals</Link>}>
          {decisions.length === 0 ? <Empty title="Nothing needs you right now">New decisions appear here the moment they arrive.</Empty> : <>
            {decisions.slice(0, SHOWN).map((d, i) => (
              <div className="decision" key={i}>
                <Sev s={d.severity} />
                <div><span className="row wrap"><b>{d.title}</b><SampleTag origin={d.origin} /></span><span className="detail">{KIND_LABEL[d.kind]} · {d.detail}</span></div>
                <button className="btn small" onClick={() => nav(d.link)}>Review <Icon.arrow size={14} /></button>
              </div>
            ))}
            {decisions.length > SHOWN && <p className="muted" style={{ fontSize: 13, margin: '8px 14px' }}>and {decisions.length - SHOWN} more</p>}
          </>}
        </Card>
        <Card title="Posture over 30 days" hint={historyHint}>
          <div style={{ padding: '6px 14px 12px' }}><LineChart points={points} label="Posture" /></div>
        </Card>
      </div>

      <Card title="Code findings" hint="From pushes and pull requests on the repositories this project holds">
        {codeFindings.length === 0
          ? <Empty title="Nothing found in pushed code">Every push and pull request is read, and any weakness it adds shows up here with its file and line.</Empty>
          : codeFindings.map((f) => <CodeFinding key={f.id} f={f} canTriage={canTriage} busy={busy === `vex-${f.id}`}
              onMark={setMarking} onWithdraw={withdraw} onChange={reload} />)}
      </Card>

      <div className="grid g2">
        <Card title="Open incidents" action={<Link to="/incidents" className="btn small">All incidents</Link>}>
          {incidents.length === 0 ? <Empty title="No open incidents" /> : incidents.map((i) => (
            <Link key={i.id} to={`/incidents/${i.id}`} className="list-item incident-row">
              <div className="incident-head"><b className="incident-title">{i.title}</b><Status s={i.status} /></div>
              <div className="incident-tags"><Sev s={i.severity} /><SampleTag origin={i.origin} /></div>
              {i.impact && <div className="muted" style={{ fontSize: 12.5 }}>Business impact: {i.impact}</div>}
            </Link>
          ))}
        </Card>
        <Card className="signals-card" title="Latest signals" hint="Last 7 days">
          {signals.length === 0 ? <Empty title="None in the last 7 days" /> : signals.map((s) => <SignalRow key={s.id} signal={s} onChange={reload} />)}
          {earlier.length > 0 && <div className="card-body" style={{ paddingTop: 4 }}>
            <div className="muted" style={{ fontWeight: 700, fontSize: 12.5, marginBottom: 6 }}>Earlier</div>
            {earlier.map((s) => <SignalRow key={s.id} signal={s} onChange={reload} />)}
          </div>}
        </Card>
      </div>
      {marking && <NotAffected finding={marking} busy={busy === `vex-${marking.id}`} onClose={() => setMarking(null)} onSave={markNotAffected} />}
    </div>
  )
}
