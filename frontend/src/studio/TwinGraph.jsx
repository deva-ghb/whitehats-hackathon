import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { useReactFlow } from '@xyflow/react'
import NetworkGraph from '../components/NetworkGraph'
import { Empty, ErrorNote, Loading } from '../components/ui'
import { humanKey } from './model'

const fitOptions = {
  padding: { top: '100px', right: '94px', bottom: '48px', left: '94px' },
  maxZoom: 1.15,
}
const narrowFit = { ...fitOptions, padding: { ...fitOptions.padding, top: '140px', left: '55px', right: '55px' } }

export const TwinGraph = forwardRef(function TwinGraph({
  document: cloneDoc, loading, error, retry, selectedIds, selectedEdge, describeItem, events,
  onStatus, findings, emptyHint,
}, ref) {
  const flow = useReactFlow()
  const canvas = useRef(null)
  const options = window.matchMedia('(max-width: 1279px)').matches ? narrowFit : fitOptions
  const groups = new Set(cloneDoc?.groups.map(group => group.id))
  const items = [...(cloneDoc?.groups || []), ...(cloneDoc?.nodes || [])].map(item => ({
    id: item.id, title: item.name || humanKey(item.id),
    detail: describeItem(item.id, item).ariaLabel,
    className: [
      groups.has(item.id) && 'network-boundary',
      findings.has(item.id) && 'network-alert',
    ].filter(Boolean).join(' '),
  }))
  const names = new Map(items.map(item => [item.id, item.title]))
  const rawEdges = (cloneDoc?.edges || []).filter(edge => names.has(edge.from) && names.has(edge.to))
  const idOf = edge => JSON.stringify([edge.from, edge.to, edge.type])
  const edges = rawEdges.map(edge => ({
    id: idOf(edge), source: edge.from, target: edge.to,
    ariaLabel: `${names.get(edge.from)} ${humanKey(edge.type).toLowerCase()} ${names.get(edge.to)}`,
    detail: edge.properties?.description,
  }))

  useEffect(() => { onStatus({ laying: loading, count: items.length }) }, [onStatus, loading, items.length])
  useImperativeHandle(ref, () => ({
    fit: () => flow.fitView(options),
    relayout: () => flow.fitView(options),
    reveal(id) {
      requestAnimationFrame(() => {
        const node = flow.getNode(id)
        if (!node || !canvas.current) return
        const { x, y } = node.position
        const point = flow.flowToScreenPosition(node.position)
        const rect = canvas.current.getBoundingClientRect()
        const inside = point.x >= rect.left + 24 && point.x <= rect.right - 24
          && point.y >= rect.top + 85 && point.y <= rect.bottom - 32
        if (!inside) flow.setCenter(x, y, { zoom: flow.getZoom() })
      })
    },
  }), [flow, options])

  return (
    <div ref={canvas} className="studio-canvas" aria-busy={loading}>
      {error && <ErrorNote error={error} retry={retry} />}
      {loading && !items.length && <div className="studio-wait"><Loading rows={4} /></div>}
      {cloneDoc && !items.length && <div className="studio-wait"><Empty title="This clone is empty">{emptyHint}</Empty></div>}
      {items.length > 0 && (
        <NetworkGraph items={items} links={edges} selected={selectedIds[0]} selectedEdge={selectedEdge && idOf(selectedEdge)}
          onPick={id => id ? events.onSelect([id]) : events.onBlank()}
          onPickEdge={id => {
            const edge = rawEdges.find(edge => idOf(edge) === id)
            if (edge) events.onSelectEdge(edge)
          }}
          fitOptions={options}
          label={`Relationship graph of ${cloneDoc.clone}`} />
      )}
    </div>
  )
})
