import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, fmt } from '../lib/api'
import { useAuth } from '../App'
import { Field, Logo } from '../components/ui'

function Frame({ children, heading, sub }) {
  return (
    <div className="login">
      <div className="login-art">
        <div className="row"><div className="brand-mark"><Logo /></div><b style={{ color: '#fff', fontSize: 18 }}>WhiteHats</b></div>
        <div>
          <h1>Security your board can read and your team can act on.</h1>
          <p>One score. The decisions that need you. Fixes that are proven before anyone merges them. Many small specialists watching every corner.</p>
        </div>
        <div style={{ color: '#6e8098', fontSize: 12 }}>The Accelerated Agent Defence Layer · an Onyx capability</div>
      </div>
      <div className="login-form">
        <div className="login-box">
          <div><h2 style={{ fontSize: 24, fontWeight: 800 }}>{heading}</h2>{sub && <p className="muted" style={{ marginTop: 4 }}>{sub}</p>}</div>
          {children}
        </div>
      </div>
    </div>
  )
}

// The second step after a password: a code, or first-time setup of an authenticator app.
function SecondStep({ step, onDone }) {
  const [code, setCode] = useState('')
  const [setup, setSetup] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (step.step === 'mfa_setup') api('/api/auth/mfa/setup', { method: 'POST', body: { step_token: step.step_token } }).then(setSetup).catch((e) => setError(e.message))
  }, [step])
  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setError('')
    try {
      const path = step.step === 'mfa' ? '/api/auth/mfa' : '/api/auth/mfa/confirm'
      onDone(await api(path, { method: 'POST', body: { step_token: step.step_token, code } }))
    } catch (err) { setError(err.message) }
    setBusy(false)
  }
  return (
    <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
      {step.step === 'mfa_setup' && (
        setup ? <div className="stack" style={{ gap: 10 }}>
          <p>Scan this with an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password), then enter the 6-digit code it shows.</p>
          <div className="qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
          <div className="muted" style={{ fontSize: 12 }}>Can't scan? Enter this key: <span className="mono">{setup.secret}</span></div>
        </div> : !error && <span className="spin" />
      )}
      {step.step === 'mfa' && <p>Enter the 6-digit code from your authenticator app.</p>}
      <Field label="Code"><input id="code" className="input mono" inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code}
        onChange={(e) => setCode(e.target.value)} autoFocus required style={{ fontSize: 20, letterSpacing: '.3em' }} /></Field>
      {error && <div className="banner crit" role="alert">{error}</div>}
      <button className="btn primary" disabled={busy || code.replace(' ', '').length < 6}>{busy ? <span className="spin" /> : 'Continue'}</button>
    </form>
  )
}

export default function Login() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [step, setStep] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const handle = (res) => (res.step === 'done' ? login(res) : setStep(res))
  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try { handle(await api('/api/auth/login', { method: 'POST', body: { email, password } })) } catch (err) { setError(err.message) }
    setBusy(false)
  }
  if (step) {
    return <Frame heading={step.step === 'mfa' ? 'Two-step sign-in' : 'Set up two-step sign-in'} sub={step.step === 'mfa_setup' ? 'Required for your account. It takes a minute, once.' : ''}>
      <SecondStep step={step} onDone={handle} /></Frame>
  }
  return (
    <Frame heading="Sign in" sub="Use your WhiteHats account.">
      <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
        <Field label="Work email"><input id="email" className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
        <Field label="Password"><input id="password" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <div className="banner crit" role="alert">{error}</div>}
        <button className="btn primary" disabled={busy}>{busy ? <span className="spin" /> : 'Sign in'}</button>
        <p className="muted" style={{ fontSize: 12.5 }}>New to WhiteHats? Your administrator or WhiteHats sends you an invitation link.</p>
      </form>
    </Frame>
  )
}

export function Invite() {
  const { token } = useParams()
  const nav = useNavigate()
  const { login } = useAuth()
  const [info, setInfo] = useState(null)
  const [error, setError] = useState('')
  const [f, setF] = useState({ name: '', title: '', password: '', confirm: '' })
  const [step, setStep] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api(`/api/invites/${token}`).then((d) => { setInfo(d); setF((x) => ({ ...x, name: d.name || '' })) }).catch((e) => setError(e.message))
  }, [token])
  const finish = (res) => { if (res.step === 'done') { login(res); nav('/') } else setStep(res) }
  const submit = async (e) => {
    e.preventDefault()
    if (f.password !== f.confirm) { setError('The two passwords do not match.'); return }
    setBusy(true); setError('')
    try { finish(await api(`/api/invites/${token}/accept`, { method: 'POST', body: { name: f.name, title: f.title, password: f.password } })) } catch (err) { setError(err.message) }
    setBusy(false)
  }
  if (step) return <Frame heading="Set up two-step sign-in" sub={`${info?.company} requires it for every account.`}><SecondStep step={step} onDone={finish} /></Frame>
  if (!info) return <Frame heading="Invitation">{error ? <div className="banner crit">{error}</div> : <span className="spin" />}</Frame>
  return (
    <Frame heading={`Join ${info.company}`} sub={`You are invited as ${info.role.toUpperCase()}. This link works once and expires ${fmt.time(info.expires_at)}.`}>
      <form onSubmit={submit} className="stack" style={{ gap: 14 }}>
        <Field label="Email"><input className="input" value={info.email} disabled /></Field>
        <Field label="Your name"><input id="inv-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
        <Field label="Job title"><input id="inv-title" className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Choose a password" help="At least 12 characters. A passphrase is easiest."><input id="inv-pass" className="input" type="password" autoComplete="new-password" minLength={12} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required /></Field>
        <Field label="Type it again"><input id="inv-pass2" className="input" type="password" autoComplete="new-password" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} required /></Field>
        {error && <div className="banner crit" role="alert">{error}</div>}
        <button className="btn primary" disabled={busy}>{busy ? <span className="spin" /> : 'Create my account'}</button>
      </form>
    </Frame>
  )
}

export function Terms() {
  const { reload, logout, org } = useAuth()
  const [busy, setBusy] = useState(false)
  return (
    <Frame heading="Before you start" sub={`${org?.name} on WhiteHats`}>
      <div className="stack" style={{ gap: 12, fontSize: 13.5 }}>
        <p>By continuing you agree that:</p>
        <ul style={{ margin: 0, paddingLeft: 18 }} className="stack">
          <li>WhiteHats only reads your systems. It never changes them without a person approving each change.</li>
          <li>Your data stays in the region agreed in your contract and is handled under your data processing agreement.</li>
          <li>WhiteHats staff cannot see your data unless one of your admins grants time-limited support access.</li>
          <li>Everything you approve or change is recorded in your audit trail.</li>
        </ul>
        <div className="row"><button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); await api('/api/auth/terms', { method: 'POST' }); reload() }}>I agree</button>
          <button className="btn" onClick={logout}>Sign out</button></div>
      </div>
    </Frame>
  )
}
