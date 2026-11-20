/** A generated 500-node clone for the canvas performance check (P2.5). Pure: the same document every time. */

const NODES_PER_NETWORK = 49
const NETWORKS = 10

const KINDS = [
  // [type, how many per network, name prefix, the properties that make it Described]
  ['service.function@2', 20, 'fn', (n, k) => ({ platform: 'aws_lambda', runtime: 'python3.12', package_type: 'zip', memory_mb: 256 })],
  ['data.dynamodb@1', 10, 'table', (n, k) => ({ table_name: `table-${n}-${k}`, region: 'ap-southeast-2' })],
  ['messaging.sqs@1', 8, 'queue', (n, k) => ({ queue_name: `queue-${n}-${k}`, region: 'ap-southeast-2' })],
  ['data.s3@1', 6, 'bucket', (n, k) => ({ bucket_name: `bucket-${n}-${k}`, region: 'ap-southeast-2' })],
  ['identity.iam-role@1', 5, 'role', (n, k) => ({ role_name: `role-${n}-${k}` })],
]

const provenance = { kind: 'drawn', source: 'canvas-500' }

/** 500 nodes in 10 networks inside one account, about 800 connections, every connection one the library allows. */
export function estate500(name) {
  const nodes = []
  const edges = []
  const groups = [{ id: 'g_account', type: 'boundary.aws-account@1', name: 'generated account', kind: 'account', environment: 'prod', provenance,
    properties: { account_id: '111111111111' } }]
  for (let n = 0; n < NETWORKS; n += 1) {
    const group = `g_net_${n}`
    groups.push({ id: group, type: 'boundary.vpc@1', name: `network ${n}`, kind: 'vpc', environment: 'prod', parent: 'g_account', provenance,
      properties: { cloud: 'aws', cidr: [`10.${n}.0.0/16`], region: 'ap-southeast-2' } })
    const made = {}
    for (const [type, count, prefix, properties] of KINDS) {
      made[prefix] = []
      for (let k = 0; k < count; k += 1) {
        const id = `n_${prefix}_${n}_${k}`
        made[prefix].push(id)
        nodes.push({ id, type, name: `${prefix}-${n}-${k}`, environment: 'prod', location: group, provenance, owner: 'Generated', properties: properties(n, k) })
      }
    }
    const link = (from, type, to) => edges.push({ from, to, type })
    made.fn.forEach((fn, k) => {
      link(fn, 'runs_as', made.role[k % made.role.length])
      link(fn, 'reads', made.table[k % made.table.length])
      if (k % 2 === 0) link(fn, 'writes', made.table[(k + 3) % made.table.length])
      if (k % 3 === 0) link(fn, 'publishes_to', made.queue[k % made.queue.length])
      if (k % 4 === 0) link(fn, 'writes', made.bucket[k % made.bucket.length])
      if (k % 5 === 0) link(fn, 'calls', made.fn[(k + 7) % made.fn.length])
    })
  }
  // Ten gateways outside the networks, each calling the first functions of its own network: 500 nodes in all.
  for (let n = 0; n < NETWORKS; n += 1) {
    const id = `n_api_${n}`
    nodes.push({ id, type: 'edge.api-gateway@1', name: `api-${n}`, environment: 'prod', provenance, owner: 'Generated',
      properties: { api_id: `api-${n}`, api_type: 'http', region: 'ap-southeast-2' } })
    for (let k = 0; k < 3; k += 1) edges.push({ from: id, to: `n_fn_${n}_${k}`, type: 'calls' })
  }
  if (nodes.length !== NODES_PER_NETWORK * NETWORKS + NETWORKS) throw new Error('the generated clone is not 500 nodes')
  return { schema_version: 1, clone: name, environment: 'prod', library_version: '2026.10',
    version: { number: 1, author: 'generated', reason: 'A 500-node clone for the canvas performance check' },
    nodes, edges, groups, access_paths: [], credential_refs: [] }
}
