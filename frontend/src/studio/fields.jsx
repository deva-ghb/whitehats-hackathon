// Property fields in the inspector, one control per value kind. Secret and derived stay read-only.
import { useEffect, useState } from 'react'
import { Field, Select } from '../components/ui'
import { listFromText, numberFromText, urlProblem } from './edit'
import { formatValue, propertyRows } from './model'

export function PropertyEditor({ item, type, names, onProperty }) {
  const commit = (key, value) => onProperty(item.id, key, value)
  return propertyRows(item, type).map((group) => (
    <section key={group.tier}>
      <h4 className="inspector-tier">{group.label}</h4>
      {group.tier === 'recommended' && (
        <p className="faint" style={{ fontSize: 12.5, margin: '0 0 8px' }}>These improve the analysis. A node can be Described without them.</p>
      )}
      <div className="inspector-fields">
        {group.rows.map((row) => (
          <PropertyField key={row.key} row={row} item={item} names={names} onCommit={(value) => commit(row.key, value)} />
        ))}
      </div>
    </section>
  ))
}

function PropertyField({ row, item, names, onCommit }) {
  const id = `prop-${row.key}`
  if (row.tier === 'secret') {
    const bound = item.credential ? names.get(item.credential) || item.credential : 'Not bound yet'
    const scope = row.scopes?.length ? `Minimum scope: ${row.scopes.join(', ')}` : 'Credentials stay in the vault, not on the canvas.'
    return <ReadRow label="Credential" value={bound} note={scope} />
  }
  if (row.tier === 'derived') {
    return <ReadRow label={row.label} value={row.value === undefined ? 'Not set' : formatValue(row.value)} note="Filled by import or the Runner." />
  }
  if (row.valueType === 'enum') return <EnumField id={id} label={row.label} value={row.value} options={row.options || []} onCommit={onCommit} />
  if (row.valueType === 'boolean') return <BooleanField id={id} label={row.label} value={row.value} onCommit={onCommit} />
  if (row.valueType === 'number') return <NumberField id={id} label={row.label} value={row.value} onCommit={onCommit} />
  if (row.valueType === 'url') return <UrlField id={id} label={row.label} value={row.value} onCommit={onCommit} />
  if (row.valueType === 'list') return <ListField id={id} label={row.label} value={row.value} onCommit={onCommit} />
  return <StringField id={id} label={row.label} value={row.value} onCommit={onCommit} />
}

function ReadRow({ label, value, note }) {
  return (
    <div className="field">
      <span className="select-label">{label}</span>
      <span>{value}</span>
      {note && <span className="help">{note}</span>}
    </div>
  )
}

function EnumField({ id, label, value, options, onCommit }) {
  return (
    <Field label={label}>
      <Select id={id} value={value ?? null} onChange={onCommit} options={options.map((option) => ({ value: option, label: option }))} />
    </Field>
  )
}

function BooleanField({ id, label, value, onCommit }) {
  const current = value === true ? 'yes' : value === false ? 'no' : null
  return (
    <Field label={label}>
      <Select id={id} value={current} onChange={(next) => onCommit(next === 'yes')} options={[{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }]} />
    </Field>
  )
}

function StringField({ id, label, value, onCommit }) {
  return (
    <Field label={label}>
      <input id={id} className="input" autoComplete="off" value={value ?? ''}
        onChange={(e) => onCommit(e.target.value)}
        onBlur={(e) => onCommit(e.target.value.trim())} />
    </Field>
  )
}

function NumberField({ id, label, value, onCommit }) {
  return (
    <LooseField id={id} label={label} value={value == null ? '' : String(value)} onCommit={onCommit}
      parse={(raw) => numberFromText(raw)} />
  )
}

function UrlField({ id, label, value, onCommit }) {
  return (
    <LooseField id={id} label={label} value={value ?? ''} onCommit={onCommit}
      parse={(raw) => {
        const next = raw.trim()
        const problem = urlProblem(next)
        if (problem) return { error: problem }
        return { value: next || undefined }
      }} />
  )
}

function ListField({ id, label, value, onCommit }) {
  return (
    <LooseField id={id} label={label} value={Array.isArray(value) ? value.join(', ') : ''} onCommit={onCommit}
      parse={(raw) => {
        const items = listFromText(raw)
        return { value: items.length ? items : undefined }
      }} />
  )
}

// Keeps what the person typed when it is not a legal value yet, and says why.
function LooseField({ id, label, value, onCommit, parse }) {
  const [text, setText] = useState(value)
  const [problem, setProblem] = useState(null)
  useEffect(() => {
    if (document.activeElement?.id === id || problem) return
    setText(value)
  }, [value, id, problem])
  const apply = (raw) => {
    const parsed = parse(raw)
    if (parsed.error) {
      setProblem(parsed.error)
      onCommit(undefined)
      return
    }
    setProblem(null)
    onCommit(parsed.value)
  }
  return (
    <div>
      <Field label={label}>
        <input id={id} className="input" autoComplete="off" aria-invalid={problem ? true : undefined} value={text}
          onChange={(e) => { setText(e.target.value); apply(e.target.value) }}
          onBlur={() => apply(text)} />
      </Field>
      {problem && <p className="field-problem" role="alert">{problem}</p>}
    </div>
  )
}
