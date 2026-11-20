import { useState } from 'react'
import KnowledgeGraph, { relationshipName } from './KnowledgeGraph'
import { Link } from 'react-router-dom'
import { useAuth } from '../App'
import { Card, Disclosure, Empty, ErrorNote, Loading, Sev } from '../components/ui'
import { fmt, getProjectSlug, useApi } from '../lib/api'
import SphereGlobe from './SphereGlobe'

export default function Compliance() {
  const { org } = useAuth()
  const { data, loading, error, reload } = useApi('/api/compliance', { poll: 15000 })
  const [picked, setPicked] = useState(null)
  if (loading && !data) return <Loading rows={4} />
  if (error) return <ErrorNote error={error} retry={reload} />
  if (org?.local_workspace) return <ComplianceNetwork data={data} />
  const selected = data.controls.find(item => item.key === picked)
  const project = '/p/' + encodeURIComponent(getProjectSlug())
  return <div className="stack">
    <SphereGlobe nodes={data.nodes} edges={data.edges} selected={picked} onPick={setPicked}
      stateOf={() => ''} label="Compliance Sphere" />
    <Card title={selected ? selected.title : 'Compliance recommendations'} pad>
      {selected ? <div className="stack">
        <div><Sev s={selected.severity} /> {selected.resource}</div>
        <p>{selected.detail}</p>
        <small>Finding recorded {fmt.time(selected.observed_at)}</small>
        <button className="btn small" onClick={() => setPicked(null)}>Show all recommendations</button>
      </div> : data.controls.length ? data.controls.map(item =>
        <div key={item.key} className="list-item"><button className="btn" onClick={() => setPicked(item.key)}>{item.title}</button>
          <span className="muted"> {item.resource}</span></div>
      ) : <Empty title="No open control findings"><p>Connect cloud evidence and review the rules in your Knowledge Sphere.</p>
        <Link to={project + '/sphere'}>Open Knowledge Sphere</Link></Empty>}
    </Card>
  </div>
}

const noComparison = () => ''

function ComplianceNetwork({ data }) {
  const [picked, setPicked] = useState(null)
  const [edge, setEdge] = useState(null)
  const nodes = new Map(data.nodes.map(node => [node.key, node]))
  const node = nodes.get(picked)
  const finding = data.controls.find(control => control.key === picked)
  const relation = edge && data.edges.find(item => item.source === edge.source &&
    item.target === edge.target && item.kind === edge.kind)
  const related = node ? data.edges.filter(item => item.source === picked || item.target === picked) : []
  const pick = key => { setPicked(key); setEdge(null) }
  const pickEdge = item => { setEdge(item); setPicked(null) }
  const nodeLink = key => <button type="button" className="sphere-link" onClick={() => pick(key)}>{nodes.get(key)?.text}</button>

  return (
    <div className="sphere-body">
      <KnowledgeGraph nodes={data.nodes} edges={data.edges} selected={node?.key} onPick={pick}
        selectedEdge={relation} onPickEdge={pickEdge} stateOf={noComparison} label="Compliance Knowledge graph" />
      <aside className="sphere-inspector compliance-inspector" aria-label="Compliance details">
        {relation ? <>
          <h2>Relationship</h2>
          <p>{nodeLink(relation.source)} {relationshipName(relation.kind)} {nodeLink(relation.target)}.</p>
          <p>{relation.detail || nodes.get(relation.target)?.detail}</p>
        </> : node ? <>
          <span className="muted">{node.label}</span>
          <h2>{node.text}</h2>
          {finding ? <>
            <p><Sev s={finding.severity} /> {finding.resource}</p>
            <p>{finding.detail}</p>
            <small className="muted">Finding recorded {fmt.time(finding.observed_at)}</small>
          </> : <p>{node.detail}</p>}
          {related.length > 0 && <Disclosure key={node.key} title={`Relationships (${related.length})`} open>
            <ul className="sphere-related">
              {related.map(({ source, target, kind }) => (
                <li key={JSON.stringify([source, target, kind])}>{nodeLink(source === picked ? target : source)}</li>
              ))}
            </ul>
          </Disclosure>}
        </> : <>
          <h2>Compliance recommendations</h2>
          {data.controls.length ? <ul className="sphere-related compliance-recommendations">
            {data.controls.map(control => <li key={control.key}><Sev s={control.severity} /> {nodeLink(control.key)}</li>)}
          </ul> : <p className="muted">No open control findings.</p>}
        </>}
        {(node || relation) && <button type="button" className="btn small" onClick={() => pick(null)}>Show all recommendations</button>}
      </aside>
    </div>
  )
}
