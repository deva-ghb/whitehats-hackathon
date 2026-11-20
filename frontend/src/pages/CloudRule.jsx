import { useState } from 'react'
import { api, useApi } from '../lib/api'
import { Field, Modal, Select, useAction } from '../components/ui'

// KS.2: an approver states a business rule for the cloud: the need in words, and the check that judges the map
// against it. The things and settings offered are the ones on the map, so a rule never names a setting that is not
// there.

const OPS = [
  { value: '>=', label: 'is at least' }, { value: '<=', label: 'is at most' }, { value: '==', label: 'is exactly' },
  { value: '!=', label: 'is anything but' }, { value: 'set', label: 'is set' }, { value: 'unset', label: 'is not set' },
]
const PROTECTS = [
  { value: 'money', label: 'Money' }, { value: 'data', label: 'Data' }, { value: 'access', label: 'Access' },
  { value: 'availability', label: 'Availability' }, { value: 'other', label: 'Other' },
]
// The map's setting names, in words a business reader knows.
const SETTING_WORDS = { throttling_rps: 'Requests allowed a second', throttling_burst: 'Short burst of requests allowed',
  rate_limit: 'Rate limit', public: 'Open to the internet', encrypted: 'Encrypted', versioning: 'Keeps old versions' }
export const settingWords = (key) => SETTING_WORDS[key] || key.replace(/_/g, ' ')

export function opWords(op) { return OPS.find((o) => o.value === op)?.label || op }

export default function CloudRule({ onClose, onDone }) {
  const kinds = useApi('/api/sphere/cloud-settings')
  const [busy, run] = useAction()
  const [f, setF] = useState({ text: '', concept: '', protects: 'money', type: '', on: 'all', property: '', op: '>=', value: '' })
  const list = kinds.data || []
  const kind = list.find((k) => k.type === f.type)
  const settings = Object.keys(kind?.settings || {})
  const numeric = f.op === '>=' || f.op === '<='
  const needsValue = f.op !== 'set' && f.op !== 'unset'
  const ready = f.text.trim().length >= 5 && kind && f.property && (!needsValue || f.value.trim() !== '')
  const save = () => run('save', () => api('/api/sphere/cloud-rules', { method: 'POST', body: {
    text: f.text.trim(), concept: f.concept.trim(), protects: f.protects,
    expect: { on: f.on === 'all' ? [f.type] : [f.on], property: f.property, op: f.op,
      label: f.on === 'all' ? `every ${kind.label}` : (kind.things.find((t) => t.key === f.on)?.name || f.on),
      ...(needsValue ? { value: numeric ? Number(f.value) : f.value.trim() } : {}) },
  } }).then((r) => { if (r.status !== 'done') throw new Error(r.error || 'The rule could not be added.'); return r }),
  'Rule added: WhiteHats judges the map against it now and every hour').then((r) => { if (r) { onDone(); onClose() } })
  return (
    <Modal title="Add a business rule for the cloud" onClose={onClose} wide
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!ready || busy === 'save' || (numeric && Number.isNaN(Number(f.value)))} onClick={save}>Add the rule</button></>}>
      <div className="stack" style={{ gap: 14 }}>
        <p className="muted">Only the business knows what it needs: say it once, and WhiteHats checks the cloud against it every hour. A thing breaking it shows as a finding on it.</p>
        <Field label="The rule, in words"><input id="rule-text" className="input" value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="Checkout allows at least 50 requests a second" /></Field>
        <div className="grid g2">
          <Field label="What it is about (optional)"><input id="rule-concept" className="input" value={f.concept} onChange={(e) => setF({ ...f, concept: e.target.value })} placeholder="Payment" /></Field>
          <Select id="rule-protects" label="Breaking it costs" value={f.protects} onChange={(v) => setF({ ...f, protects: v })} options={PROTECTS} />
        </div>
        {kinds.loading && !kinds.data ? <p className="faint">Reading the map…</p> : !list.length ? <p className="muted">The map is empty: connect a source first, so there is something to check.</p> : (
          <>
            <div className="grid g2">
              <Select id="rule-type" label="Which things on the map" value={f.type} placeholder="Choose a kind of thing"
                onChange={(v) => setF({ ...f, type: v, on: 'all', property: '' })}
                options={list.map((k) => ({ value: k.type, label: k.label, description: `${k.things.length} on the map` }))} />
              <Select id="rule-on" label="All of them, or one" value={f.on} disabled={!kind} onChange={(v) => setF({ ...f, on: v })}
                options={[{ value: 'all', label: `Every ${kind ? kind.label : 'one'}` }, ...(kind?.things || []).map((t) => ({ value: t.key, label: t.name }))]} />
            </div>
            <div className="grid g3">
              <Select id="rule-property" label="Setting" value={f.property} disabled={!kind} placeholder={kind && !settings.length ? 'The map holds no settings for these' : 'Choose a setting'}
                onChange={(v) => setF({ ...f, property: v })}
                options={settings.map((s) => ({ value: s, label: settingWords(s), description: `now, for example: ${String(kind.settings[s])}` }))} />
              <Select id="rule-op" label="Must be" value={f.op} onChange={(v) => setF({ ...f, op: v })} options={OPS} />
              {needsValue && <Field label="Value"><input id="rule-value" className="input" inputMode={numeric ? 'numeric' : 'text'} value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} placeholder={numeric ? '50' : 'private'} /></Field>}
            </div>
          </>
        )}
      </div>
    </Modal>
  )
}
