import { useEffect, useRef, useState } from 'react'
import { api, fmt, useApi } from '../lib/api'
import { ErrorNote, Icon, useToast } from '../components/ui'
import Prose from '../components/Prose'

export default function Assistant() {
  const toast = useToast()
  const { data, error, setData } = useApi('/api/assistant/history')
  const suggestions = useApi('/api/assistant/suggestions')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const log = useRef(null)
  // Scroll the message list itself; scrollIntoView would also scroll the page and shift the whole layout.
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight }, [data, busy])

  const ask = async (question) => {
    const text = (question ?? q).trim()
    if (!text || busy) return
    setQ('')
    setBusy(true)
    setData((d) => [...(d || []), { id: `u${Date.now()}`, role: 'user', text }])
    try {
      const reply = await api('/api/assistant/ask', { method: 'POST', body: { question: text } })
      setData((d) => [...(d || []), reply])
    } catch (e) {
      toast(e.message, 'error')
    }
    setBusy(false)
  }
  const clear = async () => { await api('/api/assistant/history', { method: 'DELETE' }); setData([]) }

  if (error) return <ErrorNote error={error} />
  const msgs = data || []
  return (
    <div className="chat">
      <div className="chat-log" aria-live="polite" ref={log}>
        {msgs.length === 0 && (
          <div className="card card-pad" style={{ maxWidth: 760 }}>
            <h3 style={{ fontSize: 18 }}>Ask anything about your security</h3>
            <p className="muted" style={{ marginTop: 6 }}>Big Hat answers from your live data and cites what it used. It never sees raw logs: each area sends a short summary that fits the model's budget.</p>
          </div>
        )}
        {msgs.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            {m.role === 'assistant' ? <Prose text={m.text} /> : m.text}
            {m.role === 'assistant' && m.meta && (
              <div className="meta">
                <span>{m.meta.fallback ? 'Rules engine (no model connected)' : m.meta.model}</span>
                <span>{fmt.n(m.meta.context_tokens)} of {fmt.n(m.meta.budget)} context tokens</span>
                {m.meta.trimmed?.length > 0 && <span>Trimmed: {m.meta.trimmed.join(', ')}</span>}
                {m.meta.dropped?.length > 0 && <span>Left out: {m.meta.dropped.join(', ')}</span>}
                {m.meta.latency_ms > 0 && <span>{(m.meta.latency_ms / 1000).toFixed(1)}s</span>}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant"><span className="spin" /> Reading your data…</div>}
      </div>
      <div>
        <div className="suggest">{(suggestions.data || []).map((s) => <button key={s} onClick={() => ask(s)} disabled={busy}>{s}</button>)}</div>
        <form className="composer" onSubmit={(e) => { e.preventDefault(); ask() }}>
          <textarea id="question" aria-label="Your question" placeholder="Ask in plain English…" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() } }} />
          <div className="stack" style={{ gap: 6 }}>
            <button className="btn primary" disabled={busy || !q.trim()}><Icon.arrow size={16} /> Ask</button>
            {msgs.length > 0 && <button type="button" className="btn ghost small" onClick={clear}>Clear</button>}
          </div>
        </form>
      </div>
    </div>
  )
}
