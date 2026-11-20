import { useState } from 'react'
import { api } from '../lib/api'
import { allowed, useAuth } from '../App'
import { Field, Modal, useAction } from './ui'

/** FD.1: an approver says a finding is wrong. It closes, is not raised again, and teaches WhiteHats. */
export default function FalsePositive({ signal, onDone, className = 'btn sm' }) {
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, run] = useAction()
  if (!allowed(user, 'approve')) return null
  const save = () => run('fp', () => api(`/api/signals/${signal.id}/false-positive`, { method: 'POST', body: { reason } }),
    'Closed as a false positive. WhiteHats will not raise it again').then((out) => { if (out) { setOpen(false); setReason(''); onDone?.() } })
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>False positive&hellip;</button>
      {open && (
        <Modal title={`False positive: ${signal.title}`} onClose={() => setOpen(false)}
          footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button>
            <button className="btn danger" disabled={busy === 'fp' || reason.trim().length < 8} onClick={save}>Close as a false positive</button></>}>
          <p className="muted" style={{ marginTop: 0 }}>It closes, and so does the same finding anywhere else in this project. WhiteHats does not raise it again, learns it as an accepted exception, and counts it against the WhiteHat that raised it. You can reopen it later.</p>
          <Field label="Why is it not a real problem?" help="A short sentence; it goes on the record with your name.">
            <textarea id="fp-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: this is a published test key" autoFocus />
          </Field>
        </Modal>
      )}
    </>
  )
}
