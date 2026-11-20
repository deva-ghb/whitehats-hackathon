// What the Studio knows about a clone, worked out from the document and the node library. No I/O here.

export const TIERS = [
  ['required', 'Required'],
  ['recommended', 'Recommended'],
  ['optional', 'Optional'],
  ['secret', 'Credential'],
  ['derived', 'Filled by import'],
]

export const FAMILY_LABEL = {
  connections: 'Connections', boundaries: 'Boundaries', network: 'Network', compute: 'Compute', services: 'Services',
  code_and_delivery: 'Code and delivery', messaging: 'Messaging', data: 'Data', identity: 'Identity and secrets',
  observability: 'Observability', saas: 'SaaS and business systems', ai: 'AI', endpoints: 'Devices and people',
  security_controls: 'Security controls', annotations: 'Notes', whitehats: 'WhiteHats',
}

// Shown on the canvas; the structural contained_in edge is drawn as nesting instead.
export const STRUCTURAL = 'contained_in'

export function typeIndex(types) {
  return new Map((types || []).map((t) => [`${t.id}@${t.version}`, t]))
}

/** Draft until every required property is filled (design 5.4). A type the library does not hold is its own state. */
export function describe(item, type) {
  if (!type) return { state: 'unknown', missing: [] }
  // A Generic node is a placeholder until the library has a proper type for it (design 9.4).
  if (type.generic) return { state: 'generic', missing: [] }
  const values = item.properties || {}
  const missing = type.properties
    .filter((p) => p.tier === 'required' && blank(values[p.key]))
    .map((p) => p.key)
  return { state: missing.length ? 'draft' : 'described', missing }
}

export const STATE_LABEL = { draft: 'Draft', described: 'Described', unknown: 'Type not in library', generic: 'Needs a type' }

function blank(value) {
  if (value === undefined || value === null || value === '') return true
  return typeof value === 'string' && value.trim() === ''
}

/** One line per thing a person should fix, in canvas order. */
export function problems(document, types) {
  const index = typeIndex(types)
  const found = []
  for (const item of [...(document.groups || []), ...(document.nodes || [])]) {
    const type = index.get(item.type)
    const { state, missing } = describe(item, type)
    if (state === 'unknown') found.push({ id: item.id, name: item.name, text: `${item.type} is not in the node library` })
    else if (missing.length) found.push({ id: item.id, name: item.name, text: `Needs ${missing.map(humanKey).join(', ')}` })
  }
  return found
}

// Abbreviations keep their capitals when a property key becomes a label ("api_id" reads "API ID").
const ACRONYMS = { api: 'API', arn: 'ARN', id: 'ID', url: 'URL', dlq: 'DLQ', mb: 'MB', usd: 'USD', cidr: 'CIDR', iam: 'IAM', edr: 'EDR', ip: 'IP' }

export function humanKey(key) {
  const words = key.split('_').map((word) => ACRONYMS[word] || word)
  const text = words.join(' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function formatValue(value) {
  if (value === true) return 'Yes'
  if (value === false) return 'No'
  if (Array.isArray(value)) return value.join(', ')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** Properties of one item, grouped by tier, with the value when it is set. */
export function propertyRows(item, type) {
  const values = item.properties || {}
  return TIERS.map(([tier, label]) => ({
    tier, label,
    rows: (type?.properties || []).filter((p) => p.tier === tier).map((p) => ({
      key: p.key, label: humanKey(p.key), value: values[p.key], options: p.values, valueType: p.type, scopes: p.scopes_hint, tier: p.tier,
    })),
  })).filter((group) => group.rows.length)
}

/** A readable name for screen readers: "Serverless function shop-api, Draft, 1 of 2 required properties missing". */
export function accessibleName(item, type, described) {
  const kind = type?.display_name || item.type
  const missing = described.missing.length ? `, ${described.missing.length} required ${described.missing.length === 1 ? 'property' : 'properties'} missing` : ''
  return `${kind} ${item.name}, ${STATE_LABEL[described.state]}${missing}`
}
