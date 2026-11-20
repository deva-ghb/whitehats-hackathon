// Records the WhiteHats demo (DEMO.2) against the deployed server, end to end and live: nothing is mocked.
//
//   node demo/record-demo.mjs                 # records into demo-video/footage
//   REHEARSE=1 node demo/record-demo.mjs      # the same run without capturing frames (DEMO.1's dry run)
//
// Adapted from the Mediar demo recorder (sos/working-prototype/e2e/demo/record-demo.mjs): each person's screen is
// captured at 2880x1620 through the Chrome screencast with a drawn cursor, and every story beat is logged with its
// times so demo-video/build_video.py can cut the footage.
//
// Before a take (see demo-video/README.md): the WhiteHats GitHub App is uninstalled from deva-ghb, Ledgerly has no
// AWS or GitHub connection and no "Payments shop" project, and the proving ground is at pg-baseline. One step needs
// a person: GitHub refuses an automated click on its install page, so the recorder waits there for a real click.
import { chromium } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { BASE } from './ledgerly.mjs'
import { ARGS, CURSOR, OUT, REHEARSE, SCALE, Screen, VIEW, beat, beats, log, person, sleep, until, wait } from './kit.mjs'

// START_AT=invite records the rest of the story on the project an earlier take set up (its connections stay), into
// its own FOOTAGE folder; demo-video/build_video.py joins the parts. Used when a take stops after GitHub's install.
// START_AT=audit-fix starts after the onboarding audit has finished (a take that stopped while it ran).
const PG = process.env.PROVING_GROUND || path.join(os.homedir(), 'localdisk/E/whitehats-proving-ground')
const PROFILE = process.env.DEMO_PROFILE || path.join(os.homedir(), '.whitehats-demo-chrome')
const SLUG = 'payments-shop'
const REPO = 'deva-ghb/whitehats-proving-ground'
const AWS = {
  role_arn: 'arn:aws:iam::509594260364:role/whitehats-readonly', external_id: process.env.AWS_EXTERNAL_ID || '',
  region: 'ap-southeast-2', tag_key: 'project', tag_value: 'whitehats-proving-ground',
}

// gh signs in with the person's own GitHub login (its keyring), never a token from the environment.
function sh(cmd, args, env = {}) {
  log(`$ ${cmd} ${args.join(' ')}`)
  const merged = { ...process.env, ...env }
  delete merged.GITHUB_TOKEN; delete merged.GH_TOKEN
  return execFileSync(cmd, args, { cwd: PG, encoding: 'utf8', env: merged }).trim()
}
const story = (id) => sh('python3', ['scripts/story.py', 'open', id]).split('\n').pop()
const pgAws = { AWS_PROFILE: 'nativesense-pg', AWS_REGION: 'ap-southeast-2' }

const openFix = async (who) => (await who.api('GET', `/remediations?project=${SLUG}`))
const pendingFix = (rows, pick) => rows.find((r) => r.status === 'pending' && pick(r))
const prNumber = (url) => url.split('/').pop()

// ------------------------------------------------------------------ the story
async function chapterOne(A) {
  const p = A.page
  await beat('c1-signin', A, () => A.signIn('cto'))
  await beat('c1-home', A, async () => { await sleep(1200); await A.glide(820, 320); await sleep(900) })
  await beat('c1-newproject', A, async () => {
    await A.click(p.getByRole('button', { name: 'New project' }), { after: 700 })
    await A.type(p.locator('#project-name'), 'Payments shop')
    await A.select(p.locator('#project-criticality'), 'High')
    await A.click(p.getByRole('button', { name: 'Create project' }), { after: 1800 })
  })
  await beat('c1-studio', A, async () => {
    await A.go(`/p/${SLUG}/studio`, p.getByRole('button', { name: 'Connect a source' }).first())
    await A.click(p.getByRole('button', { name: 'Connect a source' }).first(), { after: 900 })
  })
  await beat('c1-aws-pick', A, () => A.click(p.locator('.connect-source', { hasText: 'AWS' }).first(), { after: 1500 }))
  await beat('c1-aws-fill', A, async () => {
    for (const [key, value] of Object.entries(AWS)) {
      const field = p.locator(`#connection-${key}`)
      if (await field.count()) await A.type(field, value, { delay: key === 'role_arn' || key === 'external_id' ? 12 : 30 })
    }
  })
  await wait('c1-aws-connect', A, async () => {
    await A.click(p.getByRole('button', { name: 'Connect', exact: true }), { after: 500 })
    await p.getByText(/Found \d+ resources/).waitFor({ timeout: 180_000 })
    await p.locator('.sc-node, [data-scene-item]').nth(4).waitFor({ timeout: 120_000 })
  })
  await beat('c1-map', A, async () => {
    await A.click(p.locator('.connection-panel').getByRole('button', { name: /close|×/i }).first(), { after: 300 }).catch(() => {})
    await sleep(1500); await A.glide(560, 420); await sleep(1800); await A.glide(760, 360); await sleep(2200)
  })
}

async function connectGitHub(A) {
  const p = A.page
  await beat('c1-github-pick', A, async () => {
    await A.click(p.getByRole('button', { name: 'Connect a source' }).first(), { after: 900 })
    await A.click(p.locator('.connect-source', { hasText: 'GitHub' }), { after: 1500 })
    const install = p.getByRole('button', { name: 'Install the GitHub App' })
    if (!(await install.waitFor({ timeout: 6000 }).then(() => true, () => false))) {
      // The inspector can stay on the connection it showed last; open GitHub's the way the app links to it.
      await A.go(`/p/${SLUG}/studio?connection=github`, install)
    }
    await A.click(install, { after: 300 })
    await p.waitForURL(/github\.com/, { timeout: 30_000 })
  })
  await wait('c1-github-page', A, async () => {
    await p.getByText('Only select repositories').waitFor({ timeout: 30_000 })
    await A.click(p.getByText('Only select repositories'), { after: 600 })
    await A.click(p.getByRole('button', { name: /Select repositories/ }), { after: 600 })
    await p.keyboard.type('whitehats-proving-ground', { delay: 40 }); await sleep(1800)
    await A.click(p.locator('#repository-menu-list button[role=menuitem]').filter({ hasText: 'whitehats-proving-ground' }).first(), { after: 600 })
    await A.click(p.getByText('Install & Authorize on your personal account'), { after: 400 })
    await A.point(p.getByRole('button', { name: 'Install & Authorize' }))
    // GitHub refuses a scripted click here, so a person clicks; their mouse is drawn like the recorder's.
    log('>>> click "Install & Authorize" in the Alex window now')
    await p.waitForURL((url) => url.href.startsWith(BASE), { timeout: 0 })
    await p.getByRole('button', { name: 'Sign out' }).waitFor({ timeout: 60_000 })
  })
  await wait('c1-github-back', A, async () => {
    await until(A, 'GitHub to be read into the map', async () => {
      const gh = (await A.api('GET', '/connectors')).find((c) => c.key === 'github')
      if (gh.status === 'available') return null
      const seen = (await A.api('GET', `/connections/github/choices?project=${SLUG}`)).repos || []
      if (seen.some((name) => name !== REPO)) {
        // Every repository was shared on GitHub's page: their names would be on camera, so the take stops here.
        throw Object.assign(new Error(`GitHub shares ${seen.length} repositories, not only ${REPO}; reset and take again`), { fatal: true })
      }
      const clones = (await A.api('GET', `/clones?project=${SLUG}`)).clones
      const doc = await A.api('GET', `/clones/${clones[0].id}?project=${SLUG}`)
      return doc.document.nodes.some((n) => String(n.type).startsWith('repo.'))
    }, { every: 10_000, limit: 10 * 60_000 })
    await sleep(2500)
  })
}

async function invitePriya(A) {
  const p = A.page
  await beat('c1-invite', A, async () => {
    await A.go(`/p/${SLUG}/settings`, p.locator('#member-email'))
    await A.type(p.locator('#member-email'), 'priya@ledgerly.example')
    await A.select(p.locator('#member-role'), 'Approver')
    await A.type(p.locator('#member-reason'), 'Approves fixes for the payments shop', { delay: 28 })
    await A.click(p.getByRole('button', { name: 'Invite to this project' }), { after: 1800 })
  })
}

// ------------------------------------------------------------------ the onboarding audit (AUD.4)
const AUDIT_DONE = new Set(['complete', 'incomplete', 'failed'])
const audits = async (who) => (await who.api('GET', `/audits?project=${SLUG}`)).audits
const onboarding = async (who) => (await audits(who)).find((a) => a.trigger === 'onboarding')

async function chapterAudit(A) {
  const p = A.page
  // Connecting the code started the first audit by itself; nobody pressed anything.
  await until(A, 'the onboarding audit to be queued', async () => onboarding(A), { every: 5_000, limit: 5 * 60_000, reload: false })
  await beat('ca-start', A, async () => {
    await A.go(`/p/${SLUG}/audits`, p.getByRole('heading', { name: 'Security audits' }))
    await sleep(1200); await A.point(p.locator('.audit-steps').first()); await sleep(3500)
  })
  await wait('ca-wait', A, async () => {
    await until(A, 'the onboarding audit to finish', async () => AUDIT_DONE.has((await onboarding(A))?.status),
      { every: 60_000, limit: 150 * 60_000 })
    await sleep(2000)
  })
  const done = await onboarding(A)
  if (done.status === 'failed') throw Object.assign(new Error(`the onboarding audit failed: ${done.error}`), { fatal: true })
  await beat('ca-results', A, async () => {
    await A.go(`/p/${SLUG}/audits`, p.locator('.audit-records').first())
    await A.point(p.locator('.audit-facts').first()); await sleep(2500)
    await A.point(p.locator('.audit-records').first()); await sleep(3000)
    await A.scrollBy(420, 1500); await sleep(3000); await A.scrollTop()
  })
}

/** The fix for the audit's finding in the order lookup (S08), from Approvals to a merged, verified pull request. */
async function chapterAuditFix(A, P, G) {
  await beat('cf-priya', P, () => P.signIn('priya'))
  await beat('cf-score', P, async () => {
    await P.go(`/p/${SLUG}`, P.page.getByText('Threat posture'))
    await P.point(P.page.getByText('Threat posture')); await sleep(3000)
  })
  const fix = await until(P, 'the fix for the audit finding in the order lookup', async () => {
    const audit = await P.api('GET', `/audits/${(await onboarding(P)).id}?project=${SLUG}`)
    const found = audit.findings.filter((f) => f.fix && !f.closed)
    const order = found.find((f) => /order/i.test(`${f.title} ${f.where}`)) || found[0]
    return order && (await P.api('GET', `/remediations/${order.fix.id}?project=${SLUG}`))
  }, { every: 10_000, limit: 10 * 60_000, reload: false })
  await beat('cf-fix', P, async () => {
    await P.go(`/p/${SLUG}/approvals/${fix.id}`, P.page.getByRole('button', { name: 'Apply Fix' }))
    await P.scrollBy(320, 1200); await sleep(3500); await P.scrollTop()
    await P.click(P.page.getByRole('button', { name: 'Apply Fix' }), { after: 2500 })
  })
  const fixPr = await waitForFixPr(P, 'cf-factory', 'main')
  await mergeOnGitHub(G, fixPr, 'cf-fixpr', 'cf-merge')
  await wait('cf-closed', P, async () => {
    await P.go(`/p/${SLUG}/approvals/${fix.id}`)
    await until(P, 'the audit fix to be verified', async () => (await P.api('GET', `/remediations/${fix.id}?project=${SLUG}`)).status === 'verified', { every: 10_000, limit: 15 * 60_000 })
    await sleep(2500)
  })
}

// ------------------------------------------------------------------ every pull request is checked (CI/CD)
async function chapterPullRequest(P, G) {
  const pr = story('S02')
  log(`S02 pull request ${pr}`)
  await beat('c2-pr', G, async () => {
    await G.go(pr, G.page.getByRole('heading', { name: /find a receipt by their name/ }))
    await sleep(1500); await G.scrollBy(380, 1400); await sleep(2500)
  })
  await wait('c2-check', G, async () => {
    await until(G, 'the WhiteHats check on the pull request', async () => G.page.getByText(/WhiteHats/).filter({ hasText: /fail|found/i }).first().isVisible(), { every: 8_000, limit: 5 * 60_000 })
    await G.go(`${pr}/checks`, G.page.getByText('WhiteHats').first())
    await sleep(4000); await G.scrollBy(300, 1200); await sleep(3000)
  })
  await beat('c2-score-down', P, async () => {
    await P.go(`/p/${SLUG}`, P.page.getByText('Threat posture'))
    await until(P, 'the score to show the open finding', async () => (await P.api('GET', `/overview?project=${SLUG}`)).posture.score < 1000,
      { every: 5_000, limit: 3 * 60_000 })
    await P.point(P.page.getByText('Threat posture')); await sleep(3000)
  })
  const fix = await until(P, 'the suggested fix for the receipt lookup', async () => pendingFix(await openFix(P), (r) => r.destination === 'Fix Factory' && /receipt/i.test(r.title)), { reload: false })
  await beat('c2-fix', P, async () => {
    await P.go(`/p/${SLUG}/approvals/${fix.id}`, P.page.getByRole('button', { name: 'Apply Fix' }))
    await P.scrollBy(320, 1200); await sleep(2200); await P.scrollTop()
    await P.click(P.page.getByRole('button', { name: 'Apply Fix' }), { after: 2500 })
  })
  const fixPr = await waitForFixPr(P, 'c2-factory', 'feature/receipt-lookup-by-name')
  await mergeOnGitHub(G, fixPr, 'c2-fixpr', 'c2-merge')
  await wait('c2-closed', P, async () => {
    await P.go(`/p/${SLUG}/approvals/${fix.id}`)
    await until(P, 'the fix to be verified', async () => (await P.api('GET', `/remediations/${fix.id}?project=${SLUG}`)).status === 'verified', { every: 10_000, limit: 10 * 60_000 })
    await sleep(2500)
  })
  await beat('c2-score-up', P, async () => {
    await P.go(`/p/${SLUG}`, P.page.getByText('Threat posture'))
    await P.point(P.page.getByText('Threat posture')); await sleep(3000)
  })
}

async function waitForFixPr(P, id, base) {
  let url = ''
  await wait(id, P, async () => {
    await P.go(`/p/${SLUG}/fix-factory`)
    url = await until(P, `Fix Factory's pull request into ${base}`, async () => {
      const open = JSON.parse(sh('gh', ['pr', 'list', '--repo', REPO, '--state', 'open', '--base', base, '--json', 'url,headRefName']))
      return open.find((row) => row.headRefName.startsWith('fix-factory/'))?.url
    }, { every: 20_000, limit: 40 * 60_000 })
    await sleep(2000)
  })
  log(`fix pull request ${url}`)
  return url
}

async function mergeOnGitHub(G, url, showId, mergeId) {
  await wait(showId, G, async () => {
    await G.go(url, G.page.getByRole('heading').first())
    await until(G, 'every check on the fix pull request to pass', async () => {
      const out = JSON.parse(sh('gh', ['pr', 'view', prNumber(url), '--repo', REPO, '--json', 'statusCheckRollup']))
      const runs = out.statusCheckRollup || []
      return runs.length > 0 && runs.every((r) => ['SUCCESS', 'SKIPPED', 'NEUTRAL'].includes(r.conclusion || r.state))
    }, { every: 20_000, limit: 30 * 60_000 })
    await G.scrollBy(500, 1500); await sleep(2500)
  })
  await beat(mergeId, G, async () => {
    const merge = G.page.getByRole('button', { name: /^Merge pull request$/ })
    await G.click(merge, { after: 900 })
    await G.click(G.page.getByRole('button', { name: /^Confirm merge$/ }), { after: 1500 })
    await G.page.getByText(/Pull request successfully merged|Merged/).first().waitFor({ timeout: 60_000 })
    await sleep(2500)
  })
}

async function chapterThree(P, G) {
  const pr = story('S13')
  log(`S13 pull request ${pr}`)
  await beat('c3-pr', G, async () => {
    await G.go(`${pr}/files`, G.page.getByText('PyPDF2').first())
    await G.point(G.page.getByText(/PyPDF2==3\.0\.1|pypdf2/i).first()); await sleep(3000)
  })
  const fix = await until(P, 'the registry fix', async () => pendingFix(await openFix(P), (r) => r.title.includes('WhiteHats Registry')), { reload: false })
  await beat('c3-finding', P, async () => {
    await P.go(`/p/${SLUG}/approvals/${fix.id}`, P.page.getByRole('button', { name: 'Apply Fix' }))
    await P.scrollBy(260, 1200); await sleep(3500); await P.scrollTop()
  })
  await beat('c3-registry', P, async () => {
    await P.go(`/p/${SLUG}/registry`, P.page.getByText(/PyPDF2 3\.0\.1\+whitehats/).first())
    await P.point(P.page.locator('.registry-proof').first()); await sleep(4000)
  })
  await beat('c3-apply', P, async () => {
    await P.go(`/p/${SLUG}/approvals/${fix.id}`, P.page.getByRole('button', { name: 'Apply Fix' }))
    await P.click(P.page.getByRole('button', { name: 'Apply Fix' }), { after: 2500 })
  })
  const fixPr = await waitForFixPr(P, 'c3-factory', 'feature/statement-upload')
  await mergeOnGitHub(G, fixPr, 'c3-fixpr', 'c3-merge')
}

async function chapterFour(P) {
  await beat('c4-drift', P, async () => {
    await P.go(`/p/${SLUG}/studio`, P.page.locator('[data-scene-item]').first())
    sh('bash', ['scenarios/S14/drift.sh'], pgAws)
    await sleep(3000)
  })
  const fix = await until(P, 'the rate-limit finding', async () => pendingFix(await openFix(P), (r) => r.destination === 'A person'), { every: 20_000, limit: 25 * 60_000, reload: false })
  await beat('c4-found', P, async () => {
    // The finding is not drawn on the map yet (a follow-up); Approvals is where it arrives, for a person.
    await P.go(`/p/${SLUG}/approvals`, P.page.getByText(/Set a rate limit on/).first())
    await P.point(P.page.getByText(/Set a rate limit on/).first()); await sleep(3500)
  })
  await beat('c4-reaudit', P, async () => {
    // AWS's change also started a re-audit of what it touches; the list shows it beside the first audit.
    await until(P, 'the re-audit after the cloud change', async () => (await audits(P)).some((a) => a.trigger === 'cloud_change'),
      { every: 10_000, limit: 10 * 60_000, reload: false })
    await P.go(`/p/${SLUG}/audits`, P.page.getByText('The live cloud account changed').first())
    await P.point(P.page.getByText('The live cloud account changed').first()); await sleep(3500)
  })
  await beat('c4-fix', P, async () => {
    await P.go(`/p/${SLUG}/approvals/${fix.id}`, P.page.getByRole('button', { name: 'Take it on' }))
    await P.scrollBy(300, 1300); await sleep(3500)
  })
  await beat('c4-take', P, async () => {
    await P.scrollTop()
    await P.click(P.page.getByRole('button', { name: 'Take it on' }), { after: 3000 })
  })
  await wait('c4-wait', P, async () => {
    sh('bash', ['scenarios/S14/restore.sh'], pgAws)
    await until(P, 'the rate-limit fix to close itself', async () => (await P.api('GET', `/remediations/${fix.id}?project=${SLUG}`)).status === 'verified', { every: 20_000, limit: 25 * 60_000 })
  })
  await beat('c4-closed', P, async () => { await P.page.reload(); await sleep(1500); await P.scrollBy(300, 1200); await sleep(3500) })
}

async function closing(P) {
  await beat('c5-score', P, async () => {
    await P.go(`/p/${SLUG}`, P.page.getByText('Threat posture'))
    await P.point(P.page.getByText('Threat posture')); await sleep(4000)
  })
}

// ------------------------------------------------------------------ run
if (!AWS.external_id) throw new Error('set AWS_EXTERNAL_ID (the external id the read-only role trusts)')
if (!REHEARSE) fs.rmSync(OUT, { recursive: true, force: true })
fs.mkdirSync(OUT, { recursive: true })
// Headed on purpose: only headed Chrome screencasts at the 2x pixel ratio, and GitHub's session in the profile is
// readable only by the headed browser that saved it.
const args = ARGS
const alexCtx = await chromium.launchPersistentContext(PROFILE, { headless: false, viewport: VIEW, deviceScaleFactor: SCALE, args, locale: 'en-US' })
await alexCtx.addInitScript(CURSOR)
const other = await chromium.launch({ headless: false, args })
const priyaCtx = await other.newContext({ viewport: VIEW, deviceScaleFactor: SCALE, locale: 'en-US' })
await priyaCtx.addInitScript(CURSOR)
const A = await Screen.open(alexCtx, 'alex')
const G = await Screen.open(alexCtx, 'github')
const P = await Screen.open(priyaCtx, 'priya')
for (const page of alexCtx.pages()) if (![A.page, G.page].includes(page)) await page.close()
let failed = null
try {
  if (process.env.START_AT === 'drift-shot') {
    // One beat again (the map at the start of chapter 4), with no change to AWS: the editor takes it from this part.
    await P.signIn('priya', { slow: false })
    await P.go(`/p/${SLUG}/studio`, P.page.locator('[data-scene-item]').first())
    await sleep(3000)  // opened off camera, so the beat starts on the drawn map
    await beat('c4-drift', P, async () => { await P.glide(700, 420); await sleep(4000); await P.glide(820, 380); await sleep(4500) })
    throw Object.assign(new Error('done'), { shot: true })
  }
  const from = process.env.START_AT || ''
  if (from === 'invite' || from === 'audit-fix') {
    await A.signIn('cto', { slow: false })
  } else {
    await chapterOne(A)
    await connectGitHub(A)
  }
  if (from !== 'audit-fix') {
    await invitePriya(A)
    await chapterAudit(A)
  }
  await chapterAuditFix(A, P, G)
  await chapterThree(P, G)
  await chapterFour(P)
  await chapterPullRequest(P, G)
  await closing(P)
} catch (e) {
  if (!e.shot) failed = e
  if (failed) log('FAILED:', e.message)
  for (const s of [A, G, P]) await s.page.screenshot({ path: path.join(OUT, `fail-${s.name}.png`) }).catch(() => {})
} finally {
  for (const s of [A, G, P]) await s.stopCapture()
  fs.writeFileSync(path.join(OUT, 'beats.json'), JSON.stringify(beats, null, 1))
  await alexCtx.close(); await other.close()
}
if (failed) process.exit(1)
log(`done: ${beats.length} beats in ${OUT}`)
