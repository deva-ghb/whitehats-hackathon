// What a person sees of an import's change set, worked out from the server's items and the node library. No I/O here.
import { FAMILY_LABEL, formatValue, humanKey } from './model'

export const KIND_LABEL = { group: 'Boundary', node: 'Resource', edge: 'Connection' }
export const ACTION_LABEL = { add: 'Add', update: 'Update', remove: 'Remove' }
export const SOURCE_LABEL = { terraform_state: 'Terraform state', connections: 'What your connections found', repository: 'What a repository holds' }

/** accept, reject or undecided, as a word for the row's status and for the filters. */
export function stateOf(item) {
  if (item.decision === 'accept') return 'accepted'
  if (item.decision === 'reject') return 'rejected'
  return item.held ? 'held' : 'todo'
}

export const FILTERS = [
  ['todo', 'To decide'], ['held', 'Needs a type'], ['accepted', 'Accepted'], ['rejected', 'Rejected'], ['all', 'All'],
]

export function counts(items) {
  const found = { todo: 0, held: 0, accepted: 0, rejected: 0, all: items.length }
  for (const item of items) found[stateOf(item)] += 1
  return found
}

export function visible(items, filter) {
  return filter === 'all' ? items : items.filter((item) => stateOf(item) === filter)
}

/** The first filter that has something in it, so a person never opens on an empty list. */
export function firstFilter(items) {
  const found = counts(items)
  return FILTERS.map(([key]) => key).find((key) => found[key] > 0) || 'all'
}

/** The words for one row: what it is called, its type, and what accepting it does. */
export function describeItem(item, types) {
  const body = item.item
  if (item.kind === 'edge') {
    const [from, to] = item.ends
    return { title: `${from} ${humanKey(body.type).toLowerCase()} ${to}`, typeName: humanKey(body.type), detail: null }
  }
  const type = types.get(body.type)
  return { title: body.name, typeName: type?.display_name || body.type, detail: changeText(item) }
}

function changeText(item) {
  if (item.action === 'remove') return ['It is no longer in the source.']
  if (item.action !== 'update') return []
  return item.changes.map((change) => `${humanKey(change.field)}: ${show(change.before)} becomes ${show(change.after)}`)
}

function show(value) {
  return value === undefined || value === null || value === '' ? 'not set' : formatValue(value)
}

/** Why a row cannot be accepted cleanly yet, in words. Empty when it can. */
export function blocker(item) {
  if (item.held && item.decision !== 'accept') return 'The node library has no type for this yet. Pick the nearest one to accept it.'
  // Before a person decides, every connection waits for its ends; that is only worth saying once it is accepted.
  if (item.decision === 'accept' && item.waits_for?.length) return `Waits for ${item.waits_for.join(' and ')}, which ${item.waits_for.length === 1 ? 'is' : 'are'} not accepted.`
  return ''
}

/** Type choices for a held item: real library types only, the item's own family first. */
export function typeChoices(library, family) {
  return (library || []).filter((type) => !type.generic)
    .map((type) => ({ value: `${type.id}@${type.version}`, label: type.display_name, group: FAMILY_LABEL[type.family] || humanKey(type.family), family: type.family }))
    .sort((a, b) => (a.family === family ? 0 : 1) - (b.family === family ? 0 : 1) || a.group.localeCompare(b.group) || a.label.localeCompare(b.label))
}

/** The keys a bulk "accept everything ready" sends: undecided, not held. */
export function readyKeys(items) {
  return items.filter((item) => item.decision === 'undecided' && !item.held).map((item) => item.key)
}
