/** Helpers for journeys that use a connection on the canvas (STUDIO.4): the Connectors screen is gone, a connection is a node in a clone. */

import { execFileSync } from 'node:child_process'
import path from 'node:path'

/** Each connector's name on screen and the group kind of its node. */
export const CONNECTORS = {
  aws: ['AWS', 'account'], github: ['GitHub', 'organisation'], cloudwatch: ['AWS CloudWatch Logs', 'account'],
  okta: ['Okta', 'third-party'], entra: ['Microsoft Entra ID', 'third-party'], google: ['Google Workspace', 'third-party'],
  azure_monitor: ['Azure Monitor', 'third-party'], gcp_logging: ['Google Cloud Logging', 'third-party'],
  jira: ['Jira', 'third-party'], crowdstrike: ['CrowdStrike', 'third-party'],
}

/** The owner sets connections up (an account right); editing a clone is a project right. Someone holding both is the owner as a project admin. */
export function grantEditing(dataDir) {
  execFileSync('python3', ['-c', `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
user = db.execute("SELECT id FROM users WHERE email = 'ceo@acme.example'").fetchone()[0]
project = db.execute("SELECT p.id FROM projects p JOIN organizations o ON o.id = p.org_id WHERE o.slug = 'acme' AND p.slug = 'main'").fetchone()[0]
db.execute("UPDATE project_members SET role = 'project_admin' WHERE user_id = ? AND project_id = ?", (user, project))
db.commit()
`, path.join(dataDir, 'whitehats.db')])
}

/**
 * A clone with a node for each of these connectors, made through the API by whoever is signed in (who must be allowed to edit
 * clones), once per set. Returns the clone's id.
 */
export async function ensureConnections(page, keys) {
  return page.evaluate(async ({ keys: wanted, table }) => {
    const joined = wanted.join('-').replaceAll('_', '-')
    const slug = joined.length > 40 ? `connections-${wanted.length}-kinds` : `connections-${joined}`  // a clone's name is at most 63 characters
    const token = localStorage.getItem('wh.token')
    const project = location.pathname.split('/')[2]
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    const read = async (response) => {
      if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
      return response.json()
    }
    const list = await read(await fetch(`/api/clones?project=${project}`, { headers }))
    const found = list.clones.find((clone) => clone.clone === slug)
    if (found) return found.id
    const library = await read(await fetch(`/api/library/types?project=${project}`, { headers }))
    const groups = wanted.map((key) => ({
      id: `g_connection_${key}`, type: `connection.${key.replaceAll('_', '-')}@1`, name: `${table[key][0]} connection`, kind: table[key][1],
      environment: 'prod', provenance: { kind: 'drawn', source: 'Clone Studio' }, properties: { connector: key },
    }))
    const document = {
      schema_version: 1, clone: slug, environment: 'prod', library_version: library.library_version,
      version: { number: 1, author: 'Test', reason: 'Connections' }, nodes: [], edges: [], groups, access_paths: [], credential_refs: [],
    }
    const made = await read(await fetch(`/api/clones?project=${project}`, { method: 'POST', headers, body: JSON.stringify({ reason: 'Connections', document }) }))
    return made.id
  }, { keys, table: CONNECTORS })
}

/** Open the Studio on that clone with this connector's node selected, the way an old Connectors link does. Returns the node's panel. */
export async function openConnection(page, baseURL, cloneId, key) {
  await page.goto(`${baseURL}/studio?clone=${cloneId}&connection=${key}`)
  const panel = page.getByRole('complementary', { name: 'Inspector' }).getByRole('region', { name: `${CONNECTORS[key][0]} connection` })
  await panel.waitFor()
  return panel
}
