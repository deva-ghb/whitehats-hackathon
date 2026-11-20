// A small placed clone and the library types the editing tests need.
export const TYPES = new Map([
  ['boundary.vpc@1', { id: 'boundary.vpc', version: 1, family: 'boundaries', display_name: 'VPC', properties: [], edges: { allowed_out: [], allowed_in: ['contained_in'] } }],
  ['boundary.aws-account@1', { id: 'boundary.aws-account', version: 1, family: 'boundaries', display_name: 'AWS account', properties: [], edges: { allowed_out: [], allowed_in: [] } }],
  ['service.backend@1', { id: 'service.backend', version: 1, family: 'services', display_name: 'Backend service', properties: [], edges: { allowed_out: ['calls', 'reads'], allowed_in: ['calls'] } }],
  ['data.rds@1', { id: 'data.rds', version: 1, family: 'data', display_name: 'RDS', properties: [], edges: { allowed_out: [], allowed_in: ['reads', 'writes'] } }],
])
export const type = (ref) => TYPES.get(ref)

export function clone(overrides = {}) {
  return {
    schema_version: 1, clone: 'test', environment: 'prod', library_version: '2026.10',
    version: { number: 1, author: 'a', reason: 'r' },
    groups: [
      { id: 'g_vpc', type: 'boundary.vpc@1', name: 'VPC', kind: 'vpc', environment: 'prod', layout: { x: 100, y: 100, width: 400, height: 300 } },
    ],
    nodes: [
      { id: 'n_api', type: 'service.backend@1', name: 'api', environment: 'prod', location: 'g_vpc', layout: { x: 24, y: 48 } },
      { id: 'n_db', type: 'data.rds@1', name: 'db', environment: 'prod', layout: { x: 700, y: 120 } },
      { id: 'n_web', type: 'service.backend@1', name: 'web', environment: 'prod', layout: { x: 700, y: 320 } },
    ],
    edges: [], access_paths: [], credential_refs: [],
    ...overrides,
  }
}

export const find = (document, id) => [...document.nodes, ...document.groups].find((item) => item.id === id)
