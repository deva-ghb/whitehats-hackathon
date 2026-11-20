// The two shapes on the canvas: an estate node and a boundary that contains others.
import { memo, useContext } from 'react'
import { Handle, NodeResizer, Position, useStore } from '@xyflow/react'
import { standing } from '../components/ConnectorParts'
import { Icon } from '../components/ui'
import { ConnectionsContext } from './connectionContext'
import { connectorKey } from './connection'
import { STATE_LABEL } from './model'
import { senseWords } from './scene/sensepoints'
import { badgeText, weaknessWords } from './weaknesses'

const SENSE_SHORT = { accepted: 'Watched', suggested: 'Watch suggested', rejected: 'Watch rejected' }

export const FAMILY_ICON = {
  connections: 'plug', boundaries: 'boundary', network: 'network', compute: 'cpu', services: 'layers', code_and_delivery: 'code',
  messaging: 'queue', data: 'database', identity: 'key', observability: 'eye', saas: 'cloud', ai: 'spark',
  endpoints: 'people', security_controls: 'check', annotations: 'note', whitehats: 'swarm',
}

export const familyIcon = (family) => Icon[FAMILY_ICON[family]] || Icon.boundary

// Semantic zoom (section 1.3, rules 4 and 15): text on screen never drops below 11 px.
// Full cards (smallest text 12 px) from 92 percent; the name alone (22 px) from 50 percent; from 28 percent the name alone again, set
// larger as the view goes further out so it still measures 11 px or more on screen (the size steps every 4 percent of zoom, so a
// zoom gesture re-draws the cards only a few times); below that, icons only.
// In the compact view the state still shows as the outline: dashed for Draft, solid for Described.
export const COMPACT_BELOW = 0.92
const NAMES_BELOW = 0.5
const ICONS_BELOW = 0.28
const NAME_STEP = 4
export const detailFor = (zoom) => {
  if (zoom < ICONS_BELOW) return 'icons'
  if (zoom < NAMES_BELOW) return `names n${Math.floor((zoom * 100) / NAME_STEP) * NAME_STEP}`
  return zoom < COMPACT_BELOW ? 'compact' : 'full'
}
const useDetail = () => useStore((state) => detailFor(state.transform[2]))

// State is never colour alone: the badge carries the word and the outline changes shape (design 5.4).
// A connector problem (CN.5) is a second badge with words, and a solid warning edge on the card.
function EstateNode({ data, selected }) {
  const { item, type, described, problems, sense, weak } = data
  const Glyph = familyIcon(type?.family)
  const detail = useDetail()
  const title = `${item.name} (${type?.display_name || item.type})${problems ? `\n${problems.join('\n')}` : ''}`
  return (
    <div className={`estate-node ${described.state} ${problems ? 'has-problem' : ''} ${selected ? 'selected' : ''} ${detail} ${linkClass(data.link)}`} title={title}>
      <Handle type="target" position={Position.Left} isConnectable={Boolean(data.editable)} />
      <DropZone editable={data.editable} />
      <span className="estate-icon"><Glyph size={18} /></span>
      {problems && <span className="estate-alert" aria-hidden="true">!</span>}
      <span className="estate-copy">
        <b>{item.name}</b>
        <span>{type?.display_name || item.type}</span>
      </span>
      <span className="estate-badges">
        <span className={`estate-state ${described.state}`}>{STATE_LABEL[described.state]}</span>
        {problems && <span className="estate-state problem">Out of date</span>}
        {sense && <span className={`estate-state sense ${sense.status}`} title={senseWords(sense.status)}>{SENSE_SHORT[sense.status]}</span>}
        {weak && <span className={`estate-state weak reach-${weak.reach}`} title={weaknessWords(weak)}>{badgeText(weak)}</span>}
      </span>
      <Handle type="source" position={Position.Right} isConnectable={Boolean(data.editable)} />
    </div>
  )
}

// While a connection is being drawn, the whole item takes it, not only the small dot (see .drop-zone in studio.css).
const DropZone = ({ editable }) => (
  <Handle id="drop" type="target" position={Position.Left} className="drop-zone" isConnectableStart={false} isConnectable={Boolean(editable)} />
)

// While a connection is being drawn, each item shows whether the library lets this kind of item be the other end.
const linkClass = (link) => (link ? `link-${link}` : '')

function BoundaryNode({ data, selected }) {
  const { item, type, described } = data
  const detail = useDetail()
  const connections = useContext(ConnectionsContext)
  const key = connectorKey(item.type)
  const connector = key && connections?.byKey.get(key)
  const stands = connector ? standing(connector) : null
  return (
    <div className={`boundary-node ${described.state} ${selected ? 'selected' : ''} ${detail} ${linkClass(data.link)}`}>
      <NodeResizer isVisible={Boolean(selected && data.editable)} minWidth={data.minSize?.width} minHeight={data.minSize?.height}
        lineClassName="resize-line" handleClassName="resize-handle" onResizeEnd={(_event, box) => data.onResize?.(item.id, box)} />
      <Handle type="target" position={Position.Left} isConnectable={Boolean(data.editable)} />
      <DropZone editable={data.editable} />
      {key
        ? <div className="boundary-label connection-label"><Icon.plug size={14} /><b>{item.name}</b><span>Connection</span>
          {stands && <span className={`estate-state ${stands.problem ? 'problem' : described.state}`}>{stands.text}</span>}</div>
        : <div className="boundary-label"><Icon.boundary size={14} /><b>{item.name}</b><span>{type?.display_name || item.kind}</span>
          <span className={`estate-state ${described.state}`}>{STATE_LABEL[described.state]}</span></div>}
      <Handle type="source" position={Position.Right} isConnectable={Boolean(data.editable)} />
    </div>
  )
}

export const NODE_TYPES = { estate: memo(EstateNode), boundary: memo(BoundaryNode) }
