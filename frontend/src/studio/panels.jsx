// The Studio's library rail, inspector and problems drawer.
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Disclosure, Empty, ErrorNote, Field, Icon, Loading, Select } from '../components/ui'
import { EDGE_TYPES, GENERIC_RELATION, edgeRefusal } from './edit'
import { PropertyEditor } from './fields'
import { EditIcon } from './icons'
import { FAMILY_LABEL, STATE_LABEL, describe, formatValue, humanKey, propertyRows } from './model'
import { familyIcon } from './nodes'
import { fmt } from '../lib/api'
import { ConnectionPanel } from './ConnectionPanel'
import { familyOf, isConnectionType } from './connection'
import { REACH_SHORT, capImages, imageName } from './weaknesses'

const matches = (type, needle) => !needle || [type.display_name, type.id, ...type.aliases, ...type.vendors]
  .some((text) => text.toLowerCase().includes(needle))

const FAMILY_ORDER = Object.keys(FAMILY_LABEL)

export function LibraryRail({ library, onPick, picked, onClose, canPlace, onDragType, onDragEnd, onPlace }) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLowerCase()
  const families = useMemo(() => {
    const byFamily = new Map()
    for (const type of library.data?.types || []) {
      if (!matches(type, needle)) continue
      const family = familyOf(type)
      if (!byFamily.has(family)) byFamily.set(family, [])
      byFamily.get(family).push(type)
    }
    // A Generic type closes its family's list: the proper types come first.
    return [...byFamily.entries()].sort(([a], [b]) => FAMILY_ORDER.indexOf(a) - FAMILY_ORDER.indexOf(b)).map(([family, types]) => [family, types.sort((a, b) =>
      (a.generic === b.generic ? a.display_name.localeCompare(b.display_name) : a.generic ? 1 : -1))])
  }, [library.data, needle])
  return (
    <aside className="studio-rail" aria-label="Node library">
      <div className="rail-head">
        <div className="row between"><h2>Node library</h2>
          {onClose && <button className="x" onClick={onClose} aria-label="Close the library">×</button>}</div>
        <label className="rail-search">
          <Icon.search size={16} />
          <input className="input" type="search" placeholder="Search the library" aria-label="Search the node library"
            value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        {canPlace && <p className="rail-hint">Drag a type onto the canvas, or press Enter on it. Press ? for every shortcut.</p>}
      </div>
      <div className="rail-body">
        {library.loading && !library.data && <Loading rows={6} />}
        {library.error && <ErrorNote error={library.error} retry={library.reload} />}
        {library.data && families.length === 0 && <Empty title="No type matches">Try a vendor or a shorter word.</Empty>}
        {families.map(([family, types]) => (
          <section key={family} className="rail-family">
            <h3>{FAMILY_LABEL[family] || family}<span className="num">{types.length}</span></h3>
            {types.map((type) => {
              const Glyph = familyIcon(familyOf(type))
              const placeable = canPlace && !type.generic
              return (
                <button key={type.id} data-type={type.id} draggable={placeable || undefined}
                  className={`rail-type ${picked === type.id ? 'on' : ''} ${type.generic ? 'generic' : ''}`} onClick={() => onPick(type)}
                  aria-pressed={picked === type.id} aria-keyshortcuts={placeable ? 'Enter' : undefined}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' || !placeable) return
                    event.preventDefault()
                    onPlace?.(type)
                  }}
                  onDragStart={(event) => {
                    event.dataTransfer.setData('text/plain', type.id)
                    event.dataTransfer.effectAllowed = 'copy'
                    onDragType?.(type)
                  }}
                  onDragEnd={() => onDragEnd?.()}>
                  <Glyph size={16} />
                  <span><b>{type.display_name}</b>{type.vendors.length > 0 && <small>{type.vendors.join(', ')}</small>}</span>
                </button>
              )
            })}
          </section>
        ))}
      </div>
    </aside>
  )
}

// The inspector shows whatever is selected: a node, a boundary, several of them, an edge or a library type.
export function Inspector({ selection, names, onClose, editing, marks, actions, connections, document, weaknesses, alerts, attribution, imageAttribution, handEditing = true, onPickRelated }) {
  return (
    <aside className="studio-inspector" aria-label="Inspector">
      <div className="inspector-head">
        <h2>Inspector</h2>
        {selection && <button className="x" onClick={onClose} aria-label="Close the inspector">×</button>}
      </div>
      {/* Focusable, so a keyboard user can scroll a long read-only selection (it may hold no control at all). */}
      <div className="inspector-body" tabIndex={0} role="region" aria-label="Selection details">
        {!selection && <Empty title="Nothing selected">{handEditing ? 'Select a node, a boundary or a connection on the canvas, or a type in the library.' : 'Select anything on the map to read what it is, where it came from and why it matters.'}</Empty>}
        {selection?.kind === 'item' && <ItemDetail key={selection.item.id} {...selection} names={names} editing={editing} handEditing={handEditing} problems={marks?.[selection.item.id]} actions={actions}
          connections={connections} document={document} marks={marks} weak={weaknesses?.[selection.item.id]} alerts={alerts?.[selection.item.id]} attribution={attribution} imageAttribution={imageAttribution} onPickRelated={onPickRelated} />}
        {selection?.kind === 'items' && <ManyDetail items={selection.items} editing={editing} actions={actions} />}
        {selection?.kind === 'edge' && <EdgeDetail {...selection} names={names} editing={editing} actions={actions} />}
        {selection?.kind === 'type' && <TypeDetail type={selection.type} onPlace={editing && !selection.type.generic ? actions.place : undefined} />}
      </div>
    </aside>
  )
}

const CRITICALITY = [
  { value: 'none', label: 'Not set' },
  { value: 'crown-jewel', label: 'Crown jewel', description: 'Losing it ends the business' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]

function ItemDetail({ item, type, groups, names, editing, handEditing, problems, actions, connections, document, marks, weak, alerts, attribution, imageAttribution, onPickRelated }) {
  const described = describe(item, type)
  const related = onPickRelated ? (document?.edges || []).filter(edge =>
    edge.from === item.id || edge.to === item.id) : []
  const isConnection = isConnectionType(item.type)
  const rows = [
    ['Type', type ? `${type.display_name} (${item.type})` : item.type],
    ['Environment', item.environment],
    item.kind && ['Boundary kind', item.kind],
    !editing && (item.location || item.parent) && ['Inside', names.get(item.location || item.parent) || item.location || item.parent],
    !editing && item.owner && ['Owner', item.owner],
    !editing && item.criticality && ['Criticality', item.criticality],
    item.data_classification?.length && ['Data', item.data_classification.join(', ')],
    item.access_path && ['Access path', names.get(item.access_path) || item.access_path],
    ['Provenance', item.provenance ? `${item.provenance.kind}, from ${item.provenance.source}` : 'Not recorded'],
    item.last_confirmed_at && ['Last confirmed', `${fmt.ago(item.last_confirmed_at)} (${new Date(item.last_confirmed_at).toLocaleString()})`],
  ].filter(Boolean)
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div>
        <h3 className="inspector-title">{item.name}</h3>
        <span className={`estate-state ${described.state}`}>{STATE_LABEL[described.state]}</span>
        {problems && <div className="banner warn slim inspector-problem" role="note">{problems.map((text) => <div key={text}>{text}</div>)}</div>}
        {alerts?.length > 0 && <Disclosure title={`Open alerts (${alerts.length})`} open={onPickRelated ? undefined : true}>
          <CloudAlerts alerts={alerts} />
        </Disclosure>}
        {described.state === 'generic' && <p className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>A Generic node: shown here, but it cannot go into a clone until the library has a type for it.</p>}
        {described.missing.length > 0 && <p className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
          Stays Draft until {described.missing.map(humanKey).join(', ')} {described.missing.length === 1 ? 'is' : 'are'} filled.</p>}
      </div>
      {isConnection && connections && <ConnectionPanel item={item} connections={connections} document={document} editing={editing} marks={marks} />}
      {editing && <NameField item={item} onName={actions.name} />}
      {editing && (
        <div className="stack" style={{ gap: 12 }}>
          <TextSetting id="item-owner" label="Owner" value={item.owner} onCommit={(value) => actions.field(item.id, 'owner', value)}
            help="The team or person who answers for this." />
          <TextSetting id="item-notes" label="Notes" value={item.notes} multiline onCommit={(value) => actions.field(item.id, 'notes', value)}
            help="Anything the next person needs to know. Never paste a secret here." />
          {!isConnection && <Select id="item-inside" label="Inside" value={item.location || item.parent || 'top'} options={groups}
            onChange={(next) => actions.parent(item.id, next === 'top' ? null : next)} />}
          <Select id="item-criticality" label="Criticality" value={item.criticality || 'none'} options={CRITICALITY}
            onChange={(next) => actions.field(item.id, 'criticality', next === 'none' ? '' : next)} />
        </div>
      )}
      {editing && (
        <div className="inspector-actions">
          <button className="btn small" onClick={actions.connect} aria-keyshortcuts="C"><EditIcon.link size={14} />Draw a connection</button>
          <button className="btn small" onClick={actions.remove} aria-keyshortcuts="Delete"><EditIcon.trash size={14} />Remove</button>
        </div>
      )}
      {related.length > 0 && <Disclosure title={`Relationships (${related.length})`}>
        <div className="stack">
          {related.map(edge => {
            const outgoing = edge.from === item.id
            const other = outgoing ? edge.to : edge.from
            return <div className="stack twin-relation-entry" key={`${edge.from}/${edge.type}/${edge.to}`}>
              <button className="btn small twin-relation" onClick={() => onPickRelated(other)}>
                <span>{outgoing ? 'To' : 'From'} {names.get(other) || other}</span>
                <small>{humanKey(edge.type)}</small>
              </button>
              {edge.properties?.description && <small className="muted">{edge.properties.description}</small>}
              {edge.properties?.data && <small className="muted">Data flow: {edge.properties.data}</small>}
            </div>
          })}
        </div>
      </Disclosure>}
      {weak && <Weaknesses weak={weak} attribution={attribution} imageAttribution={imageAttribution} />}
      {type && handEditing && <ConnectionRules type={type} />}
      <Disclosure title="Properties" open={onPickRelated ? undefined : true}>
        <dl className="kv inspector-kv">{rows.map(([k, v]) => <Pair key={k} k={k} v={v} />)}</dl>
        {!editing && item.notes && <p className="inspector-note">{item.notes}</p>}
        {editing && type && !isConnection && <PropertyEditor item={item} type={type} names={names} onProperty={actions.property} />}
        {!editing && type && propertyRows(item, type).map((group) => (
          <section key={group.tier}>
            <h4 className="inspector-tier">{group.label}</h4>
            <dl className="kv inspector-kv">
              {group.rows.map((row) => group.tier === 'secret'
                ? <Pair key={row.key} k="Credential" v={item.credential ? names.get(item.credential) || item.credential : 'Not bound yet'} />
                : <Pair key={row.key} k={row.label} v={row.value === undefined ? <span className="faint">Not set</span> : formatValue(row.value)} />)}
            </dl>
          </section>
        ))}
      </Disclosure>
    </div>
  )
}

/** SC.1: the node's known package and image weaknesses, most urgent first, with why its reach is what it is.
 *  An image can carry a hundred: its worst few are listed, and how many more there are. */
/** Open alerts WhiteHats raised from the cloud account's own record of changes to this resource, newest first. Each
 * closes by itself when the record shows the change undone. */
function CloudAlerts({ alerts }) {
  return (
    <section className="inspector-alerts" aria-label="Open alerts">
      {alerts.map((alert) => (
        <div key={alert.id} className={`banner slim inspector-alert ${alert.severity === 'critical' || alert.severity === 'high' ? 'crit' : 'warn'}`} role="note">
          <strong>{alert.title}</strong>
          <span className="faint">{alert.severity} · {fmt.ago(alert.at)}</span>
          {alert.incident_id
            ? <Link to={`/incidents/${alert.incident_id}`}>Open its incident</Link>
            : <Link to="/">See it in Latest signals</Link>}
        </div>
      ))}
    </section>
  )
}

function Weaknesses({ weak, attribution, imageAttribution }) {
  const { shown, more } = capImages(weak.weaknesses)
  const images = [...new Set(weak.weaknesses.filter((row) => row.image).map((row) => row.image))]
  return (
    <section aria-label="Known weaknesses">
      <h4 className="inspector-tier">Known weaknesses</h4>
      <p className="weak-why"><span className={`estate-state weak reach-${weak.reach}`}>{REACH_SHORT[weak.reach]}</span>{' '}
        {weak.reach === 1 ? `${weak.route}.` : weak.why}</p>
      {images.map((image) => (
        <p key={image} className="weak-image">Image <span title={image}>{imageName(image)}</span></p>
      ))}
      <ol className="weak-list">
        {shown.map((row) => (
          <li key={`${row.advisory}|${row.package}`}>
            <div className="weak-row-head">
              <a href={row.url} target="_blank" rel="noreferrer">{row.advisory}</a>
              <span className={`pill ${row.severity === 'critical' ? 'crit' : row.severity === 'high' ? 'warn' : ''}`}>{row.severity}</span>
            </div>
            <div>{row.package} {row.version}{row.image && (images.length > 1 ? ` in ${imageName(row.image)}` : ', in the image')}:{' '}
              {row.fixed_versions.length ? `fixed in ${row.fixed_versions.join(', ')}` : 'no fixed version yet'}</div>
            {row.aliases.length > 0 && <div className="faint">{row.aliases.join(', ')}</div>}
            {row.summary && <div className="muted">{row.summary}</div>}
          </li>
        ))}
      </ol>
      {more.map(([image, count]) => (
        <p key={image} className="muted weak-more">And {count} more in {images.length > 1 ? imageName(image) : 'the image'}.</p>
      ))}
      {attribution && shown.some((row) => !row.image) && <p className="faint weak-source">{attribution}</p>}
      {imageAttribution && shown.some((row) => row.image) && <p className="faint weak-source">{imageAttribution}</p>}
    </section>
  )
}

const typeNames = (list) => (list.length ? list.map(humanKey).join(', ') : 'Nothing typed')

/** What the library lets this type start and receive. A Generic relation is the way round both lists. */
function ConnectionRules({ type }) {
  return (
    <section>
      <h4 className="inspector-tier">Connections the library allows</h4>
      <dl className="kv inspector-kv">
        <Pair k="Can start" v={typeNames(type.edges?.allowed_out || [])} />
        <Pair k="Can receive" v={typeNames(type.edges?.allowed_in || [])} />
      </dl>
      <p className="faint" style={{ fontSize: 12.5, margin: '6px 0 0' }}>A Generic relation can always join any two items.</p>
    </section>
  )
}

function TextSetting({ id, label, value, onCommit, multiline, help }) {
  const [text, setText] = useState(value || '')
  useEffect(() => {
    if (document.activeElement?.id === id) return
    setText(value || '')
  }, [value, id])
  const Tag = multiline ? 'textarea' : 'input'
  return (
    <Field label={label} help={help}>
      <Tag id={id} className="input" autoComplete="off" rows={multiline ? 3 : undefined} value={text}
        onChange={(e) => { setText(e.target.value); onCommit(e.target.value) }}
        onBlur={() => onCommit(text.trim())} />
    </Field>
  )
}

function NameField({ item, onName }) {
  const [text, setText] = useState(item.name)
  useEffect(() => {
    if (document.activeElement?.id === 'item-name') return
    setText(item.name)
  }, [item.name])
  return (
    <Field label="Name">
      <input id="item-name" className="input" autoComplete="off" value={text}
        onChange={(e) => { setText(e.target.value); if (e.target.value.trim()) onName(item.id, e.target.value) }}
        onBlur={() => { if (text.trim()) onName(item.id, text.trim()); else setText(item.name) }} />
    </Field>
  )
}

const ALIGN_TOOLS = [
  ['left', 'Align left edges', 'alignLeft'], ['center', 'Align centres', 'alignCenter'], ['right', 'Align right edges', 'alignRight'],
  ['top', 'Align top edges', 'alignTop'], ['middle', 'Align middles', 'alignMiddle'], ['bottom', 'Align bottom edges', 'alignBottom'],
]

function ManyDetail({ items, editing, actions }) {
  const shown = items.slice(0, 8)
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div>
        <h3 className="inspector-title">{items.length} items selected</h3>
        <ul className="inspector-list">
          {shown.map(({ item }) => <li key={item.id}>{item.name}</li>)}
          {items.length > shown.length && <li className="faint">and {items.length - shown.length} more</li>}
        </ul>
      </div>
      {editing ? (
        <>
          <section>
            <h4 className="inspector-tier">Line up</h4>
            <div className="tool-row" role="group" aria-label="Line up the selection">
              {ALIGN_TOOLS.map(([mode, label, icon]) => {
                const Glyph = EditIcon[icon]
                return <button key={mode} className="btn small icon-only" title={label} aria-label={label} onClick={() => actions.align(mode)}><Glyph size={16} /></button>
              })}
            </div>
            <div className="tool-row" role="group" aria-label="Spread the selection out">
              <button className="btn small" disabled={items.length < 3} onClick={() => actions.distribute('horizontal')}
                title={items.length < 3 ? 'Spreading needs three items' : undefined}><EditIcon.spreadAcross size={14} />Spread across</button>
              <button className="btn small" disabled={items.length < 3} onClick={() => actions.distribute('vertical')}
                title={items.length < 3 ? 'Spreading needs three items' : undefined}><EditIcon.spreadDown size={14} />Spread down</button>
            </div>
          </section>
          <div className="inspector-actions">
            <button className="btn small" onClick={actions.group} aria-keyshortcuts="Control+G"><EditIcon.group size={14} />Group in a container</button>
            <button className="btn small" onClick={actions.remove} aria-keyshortcuts="Delete"><EditIcon.trash size={14} />Remove</button>
          </div>
          <p className="faint" style={{ fontSize: 12.5, margin: 0 }}>Arrow keys move all of them together. Ctrl+Z undoes it.</p>
        </>
      ) : <p className="muted" style={{ fontSize: 12.5 }}>This clone is read only for you.</p>}
    </div>
  )
}

function EdgeDetail({ edge, fromType, toType, taken, names, editing, actions }) {
  const [problem, setProblem] = useState(null)
  useEffect(() => { setProblem(null) }, [edge.type, edge.from, edge.to])
  const ends = { fromId: edge.from, toId: edge.to, edges: taken.map((type) => ({ from: edge.from, to: edge.to, type })) }
  const why = (type) => edgeRefusal(fromType, toType, type, ends)
  const allowedNow = EDGE_TYPES.filter((type) => type !== GENERIC_RELATION && !why(type))
  const options = [...EDGE_TYPES].sort((a, b) => rank(a, why) - rank(b, why)).map((type) => ({
    value: type, label: humanKey(type), description: why(type) || (type === GENERIC_RELATION ? 'Always allowed' : 'Allowed for these two'),
  }))
  return (
    <div className="stack" style={{ gap: 12 }}>
      <h3 className="inspector-title">{humanKey(edge.type)}</h3>
      <dl className="kv inspector-kv">
        <Pair k="From" v={names.get(edge.from) || edge.from} />
        <Pair k="To" v={names.get(edge.to) || edge.to} />
        {Object.entries(edge.properties || {}).map(([k, v]) => <Pair key={k} k={humanKey(k)} v={formatValue(v)} />)}
      </dl>
      {editing && (
        <>
          <Select id="edge-kind" label="Connection type" value={edge.type} options={options}
            onChange={(next) => setProblem(actions.edgeType(next))} />
          {problem && <p className="field-problem" role="alert">{problem}</p>}
          <p className="faint" style={{ fontSize: 12.5, margin: 0 }}>
            {allowedNow.length ? `The library allows ${allowedNow.map(humanKey).join(', ')} between these two, and a Generic relation.` : 'The library allows no typed connection between these two. A Generic relation is allowed.'}
          </p>
          <div><button className="btn small" onClick={actions.remove} aria-keyshortcuts="Delete"><EditIcon.trash size={14} />Remove</button></div>
        </>
      )}
    </div>
  )
}

const rank = (type, why) => (type === GENERIC_RELATION ? 1 : why(type) ? 2 : 0)

function TypeDetail({ type, onPlace }) {
  return (
    <div className="stack" style={{ gap: 14 }}>
      {onPlace && <button className="btn primary" onClick={() => onPlace(type)}>Place on the canvas</button>}
      {type.generic && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>A Generic type is a placeholder for something the library does not know yet. It cannot go into a clone.</p>}
      <div>
        <h3 className="inspector-title">{type.display_name}</h3>
        <span className="faint mono" style={{ fontSize: 12 }}>{type.id}@{type.version}</span>
      </div>
      <dl className="kv inspector-kv">
        <Pair k="Family" v={FAMILY_LABEL[type.family] || type.family} />
        {type.vendors.length > 0 && <Pair k="Vendors" v={type.vendors.join(', ')} />}
        <Pair k="WhiteHat modes" v={type.whitehat_modes.map(humanKey).join(', ')} />
        <Pair k="ATT&CK" v={type.attack_techniques.join(', ')} />
        <Pair k="Connection test" v={type.connection_test.description} />
      </dl>
      <ConnectionRules type={type} />
      {propertyRows({}, type).map((group) => (
        <section key={group.tier}>
          <h4 className="inspector-tier">{group.label}</h4>
          <ul className="inspector-list">{group.rows.map((row) => <li key={row.key}>{row.label}{row.options && <span className="faint"> ({row.options.join(', ')})</span>}</li>)}</ul>
        </section>
      ))}
    </div>
  )
}

const Pair = ({ k, v }) => <><dt>{k}</dt><dd>{v}</dd></>

export function ProblemsDrawer({ problems, open, onToggle, onPick }) {
  return (
    <section className={`studio-drawer ${open ? 'open' : ''}`} aria-label="Problems">
      <button className="drawer-toggle" aria-expanded={open} onClick={onToggle}>
        <b>Problems</b><span className={`pill ${problems.length ? 'warn' : 'good'}`}>{problems.length ? `${problems.length} to fix` : 'None'}</span>
        <Icon.chevron size={16} />
      </button>
      {open && (
        <div className="drawer-body">
          {problems.length === 0 ? <p className="muted">Every node has a known type and its required properties.</p> : (
            <ul>{problems.map((p) => (
              <li key={p.id}><button className="btn ghost small" onClick={() => onPick(p.id)}>{p.name}</button><span>{p.text}</span></li>
            ))}</ul>
          )}
        </div>
      )}
    </section>
  )
}
