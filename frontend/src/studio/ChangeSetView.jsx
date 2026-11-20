// One import's change set: what it would add, update and remove, each waiting for a person to accept or reject it.
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmt, getProjectSlug, useApi } from '../lib/api'
import { Card, Empty, ErrorNote, Loading, Modal, Select, Tabs, useAction } from '../components/ui'
import { ACTION_LABEL, FILTERS, KIND_LABEL, SOURCE_LABEL, blocker, counts, describeItem, firstFilter, readyKeys, stateOf, typeChoices, visible } from './importing'
import { typeIndex } from './model'
import './imports.css'

const STATE_PILL = { todo: ['neutral', 'To decide'], held: ['warn', 'Needs a type'], accepted: ['good', 'Accepted'], rejected: ['neutral', 'Rejected'] }

export default function ChangeSetView({ id, editing }) {
  const detail = useApi(`/api/imports/${id}`)
  const library = useApi('/api/library/types')
  const [busy, run] = useAction()
  const [filter, setFilter] = useState(null)
  const [discarding, setDiscarding] = useState(false)
  const [applied, setApplied] = useState(null)
  const types = useMemo(() => typeIndex(library.data?.types), [library.data])
  // Open on the first list that has something in it, then stay on the person's choice as rows move between lists.
  useEffect(() => { if (detail.data && filter === null) setFilter(firstFilter(detail.data.items)) }, [detail.data, filter])
  if ((detail.loading && !detail.data) || (library.loading && !library.data)) return <Loading rows={6} />
  if (detail.error) return <ErrorNote error={detail.error} retry={detail.reload} />
  if (library.error) return <ErrorNote error={library.error} retry={library.reload} />
  const set = detail.data
  const open = set.status === 'open'
  const shown = filter ?? 'all'
  const decide = (keys, decision, type) => run(`decide-${decision}`, async () => {
    detail.setData(await api(`/api/imports/${id}/decisions`, { method: 'PUT', body: { keys, decision, ...(type ? { type } : {}) } }))
  })
  const apply = async () => {
    const out = await run('apply', () => api(`/api/imports/${id}/apply`, { method: 'POST' }))
    if (out) { setApplied(out); detail.reload() }
  }
  const discard = async () => {
    const out = await run('discard', () => api(`/api/imports/${id}/discard`, { method: 'POST' }), 'Import discarded')
    if (out) { setDiscarding(false); detail.reload() }
  }
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="page-head">
        <div>
          <Link to={`/p/${getProjectSlug()}/studio/import`} className="muted">← All imports</Link>
          <h2>{set.label}</h2>
          <p>{SOURCE_LABEL[set.source] || set.source}, read <time dateTime={set.created_at}>{fmt.ago(set.created_at)}</time> by {set.created_by}, for the clone <b>{set.clone}</b> (version {set.base_version}).</p>
        </div>
        <ImportStatus set={set} />
      </div>
      {applied && <AppliedNote done={applied} clone={set.clone} />}
      {!open && !applied && <ClosedNote set={set} />}
      {open && <Makeup now={set.now} after={set.after} />}
      {Object.keys(set.left_out).length > 0 && <LeftOut found={set.left_out} source={set.source} />}
      <Card>
        <div className="import-tools">
          <Tabs tabs={FILTERS.map(([key, label]) => [key, `${label} (${counts(set.items)[key]})`])} value={shown} onChange={setFilter} />
          {open && editing && <BulkBar items={set.items} busy={busy} onAccept={() => decide(readyKeys(set.items), 'accept')} />}
        </div>
        <ChangeTable items={visible(set.items, shown)} types={types} choices={(family) => typeChoices(library.data.types, family)}
          editable={open && editing} busy={busy} onDecide={decide} filter={shown} />
        {open && editing && <ApplyBar set={set} busy={busy} onApply={apply} onDiscard={() => setDiscarding(true)} />}
      </Card>
      {discarding && (
        <Modal title="Discard this import?" onClose={() => setDiscarding(false)} footer={(
          <><button className="btn" onClick={() => setDiscarding(false)}>Keep it</button>
            <button className="btn danger" disabled={busy === 'discard'} onClick={discard}>Discard import</button></>
        )}>
          <p>Nothing in this import has reached the clone, and nothing will. Your decisions on it are thrown away.</p>
        </Modal>
      )}
    </div>
  )
}

function ImportStatus({ set }) {
  const done = set.status !== 'open'
  return <span className={`pill ${set.status === 'applied' ? 'good' : done ? 'neutral' : 'info'}`}>
    {set.status === 'applied' ? `Applied as version ${set.applied_version}` : done ? 'Discarded' : 'Waiting for your decisions'}</span>
}

function AppliedNote({ done, clone }) {
  const { added, updated, removed, left_out_edges: leftOut } = done.report
  return (
    <div className="banner good" role="status">
      <div>
        <b>Applied as version {done.version} of {clone}.</b> {added} added, {updated} updated, {removed} removed.
        {leftOut > 0 && ` ${leftOut} ${leftOut === 1 ? 'connection was' : 'connections were'} left out because one end was not accepted.`}
        {' '}<Link to={`/p/${getProjectSlug()}/studio?clone=${done.clone_id}`}>Open it in the Studio to finish by hand</Link>.
      </div>
    </div>
  )
}

function ClosedNote({ set }) {
  return (
    <div className="banner info" role="status">
      <div>{set.status === 'applied'
        ? `This import was applied as version ${set.applied_version} of ${set.clone}. Its decisions are kept here for the record.`
        : 'This import was discarded. Nothing from it reached the clone.'}</div>
    </div>
  )
}

/** Where the clone's parts come from, now and after the accepted changes. */
function Makeup({ now, after }) {
  const row = (label, make) => (
    <div className="makeup-row">
      <div className="makeup-label">{label}</div>
      <div className="bar good" role="img" aria-label={`${make.share} percent from import`}><i style={{ width: `${make.share}%` }} /></div>
      <div className="makeup-figure num">{make.total === 0 ? 'Nothing in it yet' : <><b>{make.share}%</b> from import, {make.from_import} of {make.total}</>}</div>
    </div>
  )
  return (
    <Card title="Where the clone's parts come from" hint="Resources, boundaries and connections, by who put them there">
      <div className="card-body stack" style={{ gap: 12 }}>
        {row('Now', now)}
        {row('After the accepted changes', after)}
        <p className="muted" style={{ fontSize: 12.5 }}>
          After: {after.counts.imported} imported, {after.counts.discovered} discovered, {after.counts.drawn} drawn by hand
          {after.counts['not recorded'] > 0 && `, ${after.counts['not recorded']} with no source recorded`}.
        </p>
      </div>
    </Card>
  )
}

function LeftOut({ found, source }) {
  const total = Object.values(found).reduce((sum, n) => sum + n, 0)
  if (source === 'repository') {
    return (
      <details className="banner info left-out">
        <summary>{total === 1 ? '1 thing was read but is not drawn' : `${total} things were read but are not drawn`}</summary>
        <p>The scan read these in the repository and left them out: a project that nothing deploys, something switched off, or a file it could not read.</p>
        <ul>{Object.entries(found).map(([what, n]) => <li key={what}>{what}{n > 1 && <> <span className="num">{n}</span></>}</li>)}</ul>
      </details>
    )
  }
  return (
    <details className="banner info left-out">
      <summary>{total === 1 ? '1 setting was read but is not drawn as a box' : `${total} settings were read but are not drawn as boxes`}</summary>
      <p>They only configure another resource (a policy, an encryption setting, who may call it), so the map would be crowded with them and the library has no type for them.</p>
      <ul>{Object.entries(found).map(([type, n]) => <li key={type}><code className="mono">{type}</code> <span className="num">{n}</span></li>)}</ul>
    </details>
  )
}

function BulkBar({ items, busy, onAccept }) {
  const ready = readyKeys(items).length
  const held = items.filter((item) => item.held && item.decision !== 'accept').length
  return (
    <div className="import-bulk">
      <button className="btn small" disabled={!ready || busy === 'decide-accept'} onClick={onAccept}>Accept all {ready} that are ready</button>
      {held > 0 && <span className="muted">{held} {held === 1 ? 'needs' : 'need'} a type first.</span>}
    </div>
  )
}

function ApplyBar({ set, busy, onApply, onDiscard }) {
  const done = counts(set.items)
  return (
    <div className="import-apply">
      <p><b>{done.accepted}</b> accepted, <b>{done.rejected}</b> rejected, <b>{done.todo + done.held}</b> not decided yet.
        Only accepted changes reach the clone; a rejected one is not offered again unless it changes.</p>
      <div className="row wrap">
        <button className="btn ghost danger" onClick={onDiscard}>Discard import</button>
        <button className="btn primary" disabled={!done.accepted || busy === 'apply'} onClick={onApply}>
          {busy === 'apply' ? <span className="spin" /> : `Apply ${done.accepted} accepted ${done.accepted === 1 ? 'change' : 'changes'}`}</button>
      </div>
    </div>
  )
}

function ChangeTable({ items, types, choices, editable, busy, onDecide, filter }) {
  if (!items.length) {
    return <Empty title={filter === 'all' ? 'Nothing to review' : 'Nothing here'}>{filter === 'todo' ? 'Every change has a decision.' : 'No change is in this list.'}</Empty>
  }
  return (
    <div className="tbl-wrap tbl-stack">
      <table className="t import-table">
        <thead><tr><th>Change</th><th>Type</th><th>Status</th>{editable && <th><span className="sr-only">Decide</span></th>}</tr></thead>
        <tbody>{items.map((item) => <ChangeRow key={item.key} item={item} types={types} choices={choices} editable={editable} busy={busy} onDecide={onDecide} />)}</tbody>
      </table>
    </div>
  )
}

function ChangeRow({ item, types, choices, editable, busy, onDecide }) {
  const words = describeItem(item, types)
  const state = stateOf(item)
  const [tone, label] = STATE_PILL[state]
  const why = blocker(item)
  const [picked, setPicked] = useState(item.type || undefined)
  const held = item.held
  const family = types.get(item.item.type)?.family
  return (
    <tr className={`import-row ${state}`}>
      <td data-label="Change">
        <div className="import-title"><span className={`pill ${item.action === 'remove' ? 'warn' : 'neutral'}`}>{ACTION_LABEL[item.action]} {KIND_LABEL[item.kind].toLowerCase()}</span><b>{words.title}</b></div>
        {words.detail?.length > 0 && <ul className="import-detail">{words.detail.map((line) => <li key={line}>{line}</li>)}</ul>}
        {why && <p className="import-why">{why}</p>}
      </td>
      <td data-label="Type">
        {held && editable && item.decision !== 'accept'
          ? <div className="import-type"><Select id={`type-${item.key}`} label={`Library type for ${words.title}`} value={picked} onChange={setPicked}
            options={choices(family)} placeholder="Choose a type" /></div>
          : (held && item.type ? types.get(item.type)?.display_name : words.typeName)}
      </td>
      <td data-label="Status"><span className={`pill ${tone}`}>{label}</span></td>
      {editable && (
        <td className="import-actions">
          {item.decision === 'undecided'
            ? <>
              <button className="btn small" aria-label={`Accept ${words.title}`} disabled={(held && !picked) || busy === 'decide-accept'}
                onClick={() => onDecide([item.key], 'accept', held ? picked : undefined)}>Accept</button>
              <button className="btn small" aria-label={`Reject ${words.title}`} disabled={busy === 'decide-reject'}
                onClick={() => onDecide([item.key], 'reject')}>Reject</button>
            </>
            : <button className="btn small ghost" aria-label={`Undo the decision on ${words.title}`} disabled={busy === 'decide-undecided'}
              onClick={() => onDecide([item.key], 'undecided')}>Undo</button>}
        </td>
      )}
    </tr>
  )
}
