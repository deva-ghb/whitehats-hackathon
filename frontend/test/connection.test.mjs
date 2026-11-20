import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  CONNECTIONS_FAMILY, connectionKind, connectionNode, connectionNodes, connectorKey, familyOf, insideConnection, isConnectionType,
} from '../src/studio/connection.js'
import { placeType } from '../src/studio/edit.js'
import { absoluteBoxes, moveItems } from '../src/studio/geometry.js'
import { clone } from './fixtures.mjs'

const aws = { id: 'connection.aws', version: 1, family: 'boundaries', display_name: 'AWS connection', vendors: [], aliases: [] }
const monitor = { id: 'connection.azure-monitor', version: 1, family: 'boundaries', display_name: 'Azure Monitor connection', vendors: [], aliases: [] }

test('a connection type is told by its id, and its connector is the name the server knows it by', () => {
  assert.equal(isConnectionType('connection.aws@1'), true)
  assert.equal(isConnectionType('boundary.aws-account@1'), false)
  assert.equal(connectorKey('connection.azure-monitor@1'), 'azure_monitor')
  assert.equal(connectorKey('connection.gcp-logging'), 'gcp_logging')
  assert.equal(connectorKey('boundary.vpc@1'), null)
})

test('a connection is listed in its own section of the library, whatever family it is declared in', () => {
  assert.equal(familyOf(aws), CONNECTIONS_FAMILY)
  assert.equal(familyOf({ id: 'boundary.vpc', family: 'boundaries' }), 'boundaries')
})

test('the group kind of a connection is the schema word for what it connects', () => {
  assert.equal(connectionKind('connection.aws@1'), 'account')
  assert.equal(connectionKind('connection.cloudwatch@1'), 'account')
  assert.equal(connectionKind('connection.github@1'), 'organisation')
  assert.equal(connectionKind('connection.okta@1'), 'third-party')
})

test('placing a connection makes a container that holds the connector name and nothing else', () => {
  const { document, id } = placeType(clone({ groups: [], nodes: [] }), monitor)
  const made = document.groups.find((group) => group.id === id)
  assert.equal(made.type, 'connection.azure-monitor@1')
  assert.equal(made.kind, 'third-party')
  assert.deepEqual(made.properties, { connector: 'azure_monitor' })
  assert.deepEqual(Object.keys(made).sort(), ['environment', 'id', 'kind', 'name', 'properties', 'provenance', 'type'])
  assert.equal(made.provenance.kind, 'drawn')
})

test('the node of a connector is found by its type, and every connection of a clone by its connector', () => {
  const { document: first } = placeType(clone({ groups: [], nodes: [] }), aws)
  const { document } = placeType(first, monitor)
  assert.equal(connectionNode(document, 'aws').type, 'connection.aws@1')
  assert.equal(connectionNode(document, 'okta'), null)
  assert.deepEqual([...connectionNodes(document).keys()].sort(), ['aws', 'azure_monitor'])
})

test('what sits inside a connection is everything under it, however deep, and a loop in the nesting does not hang', () => {
  const document = clone({
    groups: [
      { id: 'g_c', type: 'connection.aws@1', name: 'AWS', kind: 'account', environment: 'prod' },
      { id: 'g_r', type: 'boundary.vpc@1', name: 'Region', kind: 'region', environment: 'prod', parent: 'g_c' },
    ],
    nodes: [
      { id: 'n_a', type: 'service.backend@1', name: 'a', environment: 'prod', location: 'g_r' },
      { id: 'n_b', type: 'service.backend@1', name: 'b', environment: 'prod' },
    ],
  })
  assert.deepEqual(insideConnection(document, document.groups[0]).map((item) => item.id).sort(), ['g_r', 'n_a'])
  const looped = { ...document, groups: [{ ...document.groups[0], parent: 'g_r' }, document.groups[1]] }
  assert.ok(insideConnection(looped, looped.groups[0]).length <= 3)
})

test('a connection dropped onto a container is not put inside it, though an ordinary node is', () => {
  const placed = clone()   // a placed clone: the VPC group sits at 100,100 and is 400 by 300
  const onTheVpc = { x: 300, y: 250 }
  const service = { id: 'service.backend', version: 1, family: 'services', display_name: 'Backend service', vendors: [], aliases: [] }
  const ordinary = placeType(placed, service, { at: onTheVpc, avoid: false })
  assert.equal(ordinary.document.nodes.find((node) => node.id === ordinary.id).location, 'g_vpc')
  const connection = placeType(placed, aws, { at: onTheVpc, avoid: false })
  const made = connection.document.groups.find((group) => group.id === connection.id)
  assert.equal(made.parent, undefined)
  assert.equal(made.location, undefined)
  // It settles in free space beside the container, not on top of it.
  const boxes = absoluteBoxes(connection.document)
  const [vpc, mine] = [boxes.get('g_vpc'), boxes.get(connection.id)]
  const overlap = mine.x < vpc.x + vpc.width && vpc.x < mine.x + mine.width && mine.y < vpc.y + vpc.height && vpc.y < mine.y + mine.height
  assert.equal(overlap, false)
})

test('a connection dragged onto a container stays at the top, while an ordinary node dragged there goes inside', () => {
  const placed = clone()   // the VPC group sits at 100,100 and is 400 by 300
  const { document: withConnection, id } = placeType(placed, aws, { at: { x: 900, y: 900 }, avoid: false })
  const moves = (ids) => new Map(ids.map((item) => [item, { x: 300, y: 250 }]))
  const dropped = moveItems(withConnection, moves([id]))
  assert.equal(dropped.groups.find((group) => group.id === id).parent, undefined)
  assert.equal(moveItems(withConnection, moves(['n_db'])).nodes.find((node) => node.id === 'n_db').location, 'g_vpc')
})
