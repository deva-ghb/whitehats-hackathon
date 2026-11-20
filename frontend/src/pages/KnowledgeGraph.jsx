import NetworkGraph from '../components/NetworkGraph'

const relationshipNames = { ABOUT: 'applies to', RELATES: 'related to', EXEMPTS: 'is an exception to', INCLUDES: 'includes' }
export const relationshipName = kind => relationshipNames[kind] || kind.toLowerCase().replaceAll('_', ' ')
const edgeId = ({ source, target, kind }) => JSON.stringify([source, target, kind])

export default function KnowledgeGraph({ nodes, edges, selected, onPick, selectedEdge, onPickEdge, label, diff, stateOf }) {
  const items = nodes.map(node => ({
    id: node.key, title: node.text, detail: `${node.text} (${node.label || node.kind})`,
    className: [
      node.kind === 'Concept' && 'network-boundary',
      node.kind === 'Exception' && 'network-exception',
      node.severity && 'network-alert',
      node.status === 'proposed' && 'network-proposed',
      `network-${stateOf(node, diff)}`,
    ].filter(Boolean).join(' '),
  }))
  const names = new Map(items.map(item => [item.id, item.title]))
  const links = edges.map(edge => ({
    id: edgeId(edge), source: edge.source, target: edge.target, detail: edge.detail,
    ariaLabel: `${names.get(edge.source)} ${relationshipName(edge.kind)} ${names.get(edge.target)}`,
  }))
  return (
    <section className="knowledge-graph" aria-label={label}>
      <NetworkGraph items={items} links={links} selected={selected} onPick={onPick} label={label}
        selectedEdge={selectedEdge && edgeId(selectedEdge)}
        onPickEdge={onPickEdge && (id => onPickEdge(edges.find(edge => edgeId(edge) === id)))} />
    </section>
  )
}
