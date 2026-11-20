// The dialogs editing needs: start a blank clone, choose the type of a connection, and choose the container for a group.
import { useEffect, useState } from 'react'
import { Field, Modal, Select } from '../components/ui'
import { EDGE_TYPES, ENVIRONMENT_OPTIONS, GENERIC_RELATION, cloneSlug, edgeRefusal, firstFit, slugProblem } from './edit'
import { isConnectionType } from './connection'
import { humanKey } from './model'

export function NewCloneDialog({ libraryVersion, busy, onCreate, onClose }) {
  const [name, setName] = useState('')
  const [environment, setEnvironment] = useState('prod')
  const [error, setError] = useState(null)
  const slug = cloneSlug(name)
  const submit = () => {
    const problem = slugProblem(slug)
    if (problem) { setError(problem); return }
    if (!libraryVersion) { setError('The node library is still loading.'); return }
    onCreate(slug, environment)
  }
  return (
    <Modal title="Start a blank clone" onClose={onClose} footer={(
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={submit} disabled={busy === 'create'}>Create clone</button>
      </>
    )}>
      <div className="stack" style={{ gap: 12 }}>
        <Field label="Clone name" help={slug ? `Saved as ${slug}.` : 'A short name. Letters, numbers and hyphens.'}>
          <input id="clone-name" className="input" autoComplete="off" autoFocus value={name}
            onChange={(e) => { setName(e.target.value); setError(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter') submit() }} />
        </Field>
        {error && <p className="field-problem" role="alert">{error}</p>}
        <Select id="clone-environment" label="Environment" value={environment} onChange={setEnvironment} options={ENVIRONMENT_OPTIONS} />
      </div>
    </Modal>
  )
}

export function ConnectDialog({ document, types, fromId, toId, names, onAdd, onClose }) {
  const items = [...(document.nodes || []), ...(document.groups || [])]
  const [to, setTo] = useState(toId || '')
  const source = items.find((item) => item.id === fromId)
  const target = items.find((item) => item.id === to)
  const sourceType = types.get(source?.type)
  const targetType = types.get(target?.type)
  const ends = { fromId, toId: to, edges: document.edges }
  const [edgeType, setEdgeType] = useState(GENERIC_RELATION)
  useEffect(() => {
    if (!to) return
    setEdgeType(firstFit(sourceType, targetType, { fromId, toId: to, edges: document.edges }))
  }, [to]) // retargeting picks a legal default; the person can still choose a refused type to see why
  const others = items.filter((item) => item.id !== fromId)
  const refusal = to ? edgeRefusal(sourceType, targetType, edgeType, ends) : null
  const options = [...EDGE_TYPES].sort((a, b) => rank(a, sourceType, targetType, ends) - rank(b, sourceType, targetType, ends))
  const fromName = names.get(fromId) || source?.name || 'this item'
  const toName = to ? (names.get(to) || target?.name) : null
  return (
    <Modal title={toName ? `Connect ${fromName} to ${toName}` : `Connect ${fromName}`} onClose={onClose} footer={(
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!to || Boolean(refusal)} onClick={() => onAdd(to, edgeType)}>Add connection</button>
      </>
    )}>
      <div className="stack" style={{ gap: 12 }}>
        {!toId && (
          <Select id="edge-target" label="Connect to" value={to || null} onChange={setTo}
            options={others.map((item) => ({ value: item.id, label: item.name, description: types.get(item.type)?.display_name }))} />
        )}
        {toId && <p style={{ margin: 0 }}>From <b>{fromName}</b> to <b>{toName}</b>.</p>}
        <Select id="edge-type" label="Connection type" value={edgeType} onChange={setEdgeType}
          options={options.map((type) => ({
            value: type,
            label: humanKey(type),
            description: describeChoice(type, sourceType, targetType, ends),
          }))} />
        {!to && <p className="faint" style={{ fontSize: 12.5, margin: 0 }}>Choose the item this connects to.</p>}
        {refusal && <p className="field-problem" role="alert">{refusal}</p>}
        {to && <p className="faint" style={{ fontSize: 12.5, margin: 0 }}>{allowedSummary(options, sourceType, targetType, ends)}</p>}
      </div>
    </Modal>
  )
}

function rank(type, sourceType, targetType, ends) {
  if (type === GENERIC_RELATION) return 1
  return edgeRefusal(sourceType, targetType, type, ends) ? 2 : 0
}

function describeChoice(type, sourceType, targetType, ends) {
  const why = edgeRefusal(sourceType, targetType, type, ends)
  if (type === GENERIC_RELATION && !why) return 'Always allowed, when no typed connection fits'
  return why || 'Allowed for these two'
}

function allowedSummary(options, sourceType, targetType, ends) {
  const fits = options.filter((type) => type !== GENERIC_RELATION && !edgeRefusal(sourceType, targetType, type, ends))
  if (!fits.length) return 'The library allows no typed connection between these two. A Generic relation is allowed.'
  return `The library allows ${fits.map(humanKey).join(', ')} between these two, and a Generic relation.`
}

/** Pick the boundary that will wrap the selected items. Only boundary types can hold others. */
export function GroupDialog({ types, count, busy, onGroup, onClose }) {
  const choices = types.filter((type) => type.family === 'boundaries' && !type.generic && !isConnectionType(type.id))
    .sort((a, b) => a.display_name.localeCompare(b.display_name))
  const [typeId, setTypeId] = useState(choices.find((type) => type.id === 'boundary.vpc')?.id || choices[0]?.id)
  const chosen = choices.find((type) => type.id === typeId)
  return (
    <Modal title={`Group ${count} items in a container`} onClose={onClose} footer={(
      <>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!chosen || busy} onClick={() => onGroup(chosen)}>Group them</button>
      </>
    )}>
      <div className="stack" style={{ gap: 12 }}>
        <p style={{ margin: 0 }}>A container draws a boundary around the selected items, such as an account, a VPC or a trust zone.</p>
        <Select id="group-type" label="Container type" value={typeId} onChange={setTypeId}
          options={choices.map((type) => ({ value: type.id, label: type.display_name, description: type.vendors.join(', ') || undefined }))} />
      </div>
    </Modal>
  )
}
