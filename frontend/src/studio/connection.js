// A connection is a node on the canvas, and what it finds sits inside it (STUDIO.4). What the Studio knows about one: no I/O here.
//
// A connection node is a container of a `connection.<name>` library type. It holds the name of its connector and nothing else:
// the role, the external id and every secret stay with the connection on the server and never go into a clone.

export const CONNECTION_PREFIX = 'connection.'
/** The rail's own section for these types; they are containers in the library (family boundaries) and in the clone. */
export const CONNECTIONS_FAMILY = 'connections'

const typeIdOf = (ref) => String(ref || '').split('@')[0]

export const isConnectionType = (ref) => typeIdOf(ref).startsWith(CONNECTION_PREFIX)

/** The connector a connection type or item stands for: `connection.azure-monitor` is `azure_monitor`. */
export const connectorKey = (ref) => (isConnectionType(ref) ? typeIdOf(ref).slice(CONNECTION_PREFIX.length).replaceAll('-', '_') : null)

/** The group kind (the clone schema's word) for a connection node: an account or organisation, else a third party. */
export function connectionKind(ref) {
  const key = connectorKey(ref)
  if (key === 'aws' || key === 'cloudwatch') return 'account'
  if (key === 'github') return 'organisation'
  return 'third-party'
}

/** The library section a type is listed in. */
export const familyOf = (type) => (isConnectionType(type.id) ? CONNECTIONS_FAMILY : type.family)

/** The node that stands for this connector in the clone, if one was placed. */
export function connectionNode(document, key) {
  return (document?.groups || []).find((group) => connectorKey(group.type) === key) || null
}

/** Every connection node of a clone, by connector key. */
export function connectionNodes(document) {
  return new Map((document?.groups || []).filter((group) => isConnectionType(group.type)).map((group) => [connectorKey(group.type), group]))
}

/** What a node inside a connection was found by it: the connection's own sub-nodes, to count and to mark. */
export function insideConnection(document, group) {
  const kids = new Map()
  for (const item of [...(document?.groups || []), ...(document?.nodes || [])]) {
    const parent = item.location || item.parent
    if (parent) kids.set(parent, [...(kids.get(parent) || []), item])
  }
  const found = []
  const seen = new Set([group.id])   // a document with a loop in its nesting must not loop here
  const walk = (id) => {
    for (const kid of kids.get(id) || []) {
      if (seen.has(kid.id)) continue
      seen.add(kid.id)
      found.push(kid)
      walk(kid.id)
    }
  }
  walk(group.id)
  return found
}
