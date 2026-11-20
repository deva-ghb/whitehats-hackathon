import { useMemo } from 'react'
import { BaseEdge, Controls, Handle, Position, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { networkLayout } from './networkLayout'
import './networkGraph.css'

function NetworkNode({ data }) {
  return (
    <>
      <Handle type="source" position={Position.Right} />
      <Handle type="target" position={Position.Left} />
      <span className="network-dot" title={data.detail} />
      <span className="network-label" title={data.detail}>{data.title}</span>
    </>
  )
}

function NetworkEdge({ id, source, target, sourceX, sourceY, targetX, targetY, data }) {
  let path
  if (source === target) {
    const reach = 54 + Math.abs(data.offset)
    path = `M ${sourceX},${sourceY} c ${reach},-${reach} ${reach},${reach} 0,0`
  } else {
    const dx = targetX - sourceX, dy = targetY - sourceY
    const distance = Math.hypot(dx, dy) || 1
    const midX = (sourceX + targetX) / 2 - dy / distance * data.offset
    const midY = (sourceY + targetY) / 2 + dx / distance * data.offset
    path = `M ${sourceX},${sourceY} Q ${midX},${midY} ${targetX},${targetY}`
  }
  return <g><title>{data.detail}</title><BaseEdge id={id} path={path} interactionWidth={12} /></g>
}

const nodeTypes = { network: NetworkNode }
const edgeTypes = { network: NetworkEdge }
const nodeOrigin = [0.5, 0.5]
const defaultFit = { padding: { left: '82px', right: '82px', top: '28px', bottom: '54px' }, maxZoom: 1.2 }

export default function NetworkGraph({
  items, links, selected, selectedEdge, onPick, onPickEdge, label, fitOptions = defaultFit,
}) {
  // Polls can replace data objects while the graph topology stays the same.
  const layoutKey = JSON.stringify([items.map(({ id, title }) => ({ id, title })),
    links.map(({ source, target }) => ({ source, target }))])
  const points = useMemo(() => networkLayout(...JSON.parse(layoutKey)), [layoutKey])
  const neighbors = new Set(selected ? [selected] : [])
  const pairs = new Map()
  const edges = links.filter(({ source, target }) => points.has(source) && points.has(target))
  for (const edge of edges) {
    const { source, target } = edge
    if (source === selected) neighbors.add(target)
    if (target === selected) neighbors.add(source)
    const key = JSON.stringify([source, target].sort())
    const pair = pairs.get(key) || []
    pair.push(edge)
    pairs.set(key, pair)
  }
  const flowEdges = [...pairs.values()].flatMap(pair => pair.map((edge, index) => {
    const active = edge.id === selectedEdge || edge.source === selected || edge.target === selected
    const muted = !active && Boolean(selected || selectedEdge)
    const offset = (index - (pair.length - 1) / 2) * 22 * (edge.source < edge.target ? 1 : -1)
    return {
      ...edge, type: 'network', data: { offset, detail: edge.detail || edge.ariaLabel }, selected: edge.id === selectedEdge,
      selectable: Boolean(onPickEdge), focusable: Boolean(onPickEdge), ariaRole: onPickEdge ? 'button' : undefined,
      className: active ? 'network-link-active' : muted ? 'network-muted' : '',
      zIndex: active ? 1 : 0,
    }
  }))
  const pickedEdge = edges.find(edge => edge.id === selectedEdge)
  const flowNodes = items.map(item => {
    const { x, y, diameter } = points.get(item.id)
    const muted = (selected && !neighbors.has(item.id)) ||
      (pickedEdge && item.id !== pickedEdge.source && item.id !== pickedEdge.target)
    return {
      id: item.id, type: 'network', position: { x, y }, width: diameter, height: diameter,
      data: item, selected: selected === item.id, ariaRole: 'button', ariaLabel: item.detail || item.title,
      domAttributes: { 'aria-pressed': selected === item.id },
      className: [item.className, muted && 'network-muted'].filter(Boolean).join(' '),
      zIndex: selected === item.id ? 3 : 2,
    }
  })
  return (
    <ReactFlow key={layoutKey} className="network-graph-flow" nodes={flowNodes} edges={flowEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      nodeOrigin={nodeOrigin} fitView fitViewOptions={fitOptions} minZoom={0.04} maxZoom={2}
      nodesDraggable={false} nodesConnectable={false} deleteKeyCode={null}
      onNodeClick={(_event, node) => onPick(node.id)}
      onNodesChange={changes => {
        const picked = changes.find(change => change.type === 'select' && change.selected)
        if (picked) onPick(picked.id)
      }}
      onEdgeClick={onPickEdge && ((_event, edge) => onPickEdge(edge.id))}
      onEdgesChange={onPickEdge && (changes => {
        const picked = changes.find(change => change.type === 'select' && change.selected)
        if (picked) onPickEdge(picked.id)
      })}
      onPaneClick={() => onPick(null)} aria-label={label}>
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  )
}
