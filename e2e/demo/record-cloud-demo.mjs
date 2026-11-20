// Records the cloud defence demo (DEMO.3) against the deployed server, live: real changes in the proving-ground AWS
// account, and what WhiteHats does about each, as Ledgerly's lead engineer, Priya, sees it. Nothing is mocked.
//
//   FOOTAGE=footage-cloud node demo/record-cloud-demo.mjs
//   REHEARSE=1 node demo/record-cloud-demo.mjs        # the same run without capturing frames
//
// The AWS changes are made here with the AWS CLI (profiles nativesense for the security group and the user,
// nativesense-pg for the bucket) and every one is undone before the take ends. The throwaway security group
// wh-demo-open-ssh must exist (tagged project=whitehats-proving-ground), the receipts bucket must be empty, and the
// unused user wh-demo-throwaway must exist with no rights: the take gives it administrator rights, then takes them
// away again. The user itself stays; it is never deleted.
//
// Priya signs in herself in the recorder window when it opens; the recorder never types a password.
import { chromium } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { ARGS, CURSOR, OUT, SCALE, Screen, VIEW, beat, beats, log, sleep, until, wait } from './kit.mjs'

const SLUG = 'payment-project'
const BUCKET = 'whitehats-proving-ground-509594260364-receipts'
const USER = 'wh-demo-throwaway'
const ADMIN = 'arn:aws:iam::aws:policy/AdministratorAccess'
const TITLES = {
  ssh: 'A firewall rule now allows traffic from the whole internet',
  admin: 'Full administrator access was granted',
  image: 'Potential secrets found in the image',
}

// What this take changed in AWS, noted the moment it is changed, so the cleanup undoes it even if a beat fails.
const made = { sg: null }
// Only incidents this take opened count: an earlier take's may still be closing.
const TAKE_START = Date.now() - 60_000
const openedNow = (i) => Date.parse(i.created_at.endsWith('Z') || i.created_at.includes('+') ? i.created_at : `${i.created_at}Z`) >= TAKE_START

function aws(profile, args) {
  log(`$ aws ${args.join(' ')}`)
  return execFileSync('aws', [...args, '--output', 'text'], {
    encoding: 'utf8', env: { ...process.env, AWS_PROFILE: profile, AWS_REGION: 'ap-southeast-2' },
  }).trim()
}
const group = () => aws('nativesense', ['ec2', 'describe-security-groups', '--filters', 'Name=group-name,Values=wh-demo-open-ssh',
  '--query', 'SecurityGroups[0].GroupId'])

const incidents = (who) => who.api('GET', `/incidents?project=${SLUG}`)
const CLOSED = ['resolved', 'false_positive']
// The open incident with this title about this resource (another incident may share the title).
const openIncident = async (who, title, about = '') => (await incidents(who)).find((i) =>
  i.title.startsWith(title) && (i.summary || '').includes(about) && !CLOSED.includes(i.status) && openedNow(i))
const incidentAbout = async (who, needle) => (await incidents(who)).find((i) =>
  i.title.includes(needle) && !CLOSED.includes(i.status) && openedNow(i))
// The image finding is older than the take, so it is found without the take's start.
const openSince = async (who, title) => (await incidents(who)).find((i) => i.title.startsWith(title) && !CLOSED.includes(i.status))
// The incidents list row for one incident, found by its reference.
const rowOf = (A, inc) => A.page.getByRole('row').filter({ hasText: inc.ref }).first()

async function overview(A) {
  await beat('c1-overview', A, async () => {
    await A.go(`/p/${SLUG}`, A.page.getByRole('heading', { name: 'Latest signals' }))
    await A.glide(560, 300); await sleep(2500)
    await A.point(A.page.getByRole('heading', { name: 'Latest signals' })); await sleep(2500)
  })
  await beat('c1-map', A, async () => {
    await A.go(`/p/${SLUG}/studio`, A.page.locator('[data-scene-item]').first())
    await sleep(2500); await A.glide(760, 420); await sleep(3500)
  })
}

async function sshOpened(A) {
  const sg = group()
  aws('nativesense', ['ec2', 'authorize-security-group-ingress', '--group-id', sg, '--protocol', 'tcp', '--port', '22', '--cidr', '0.0.0.0/0'])
  made.sg = sg
  let inc
  await wait('a-wait', A, async () => {
    await A.go(`/p/${SLUG}/incidents`)
    inc = await until(A, 'the SSH incident', () => openIncident(A, TITLES.ssh, sg), { every: 15_000, limit: 12 * 60_000 })
  })
  await beat('a-incident', A, async () => {
    await A.go(`/p/${SLUG}/incidents`, rowOf(A, inc)) // the list as it is now, with the new incident on it
    await A.click(rowOf(A, inc), { after: 1500 })
    await A.point(A.page.locator('.page-head h2')); await sleep(3000)
    await A.point(A.page.getByText('What each WhiteHat saw')); await sleep(3500)
  })
  return { sg, inc }
}

async function adminGranted(A) {
  aws('nativesense', ['iam', 'attach-user-policy', '--user-name', USER, '--policy-arn', ADMIN])
  let inc
  await wait('b-wait', A, async () => {
    await A.go(`/p/${SLUG}/incidents`)
    inc = await until(A, 'the admin incident', () => openIncident(A, TITLES.admin, USER), { every: 15_000, limit: 12 * 60_000 })
  })
  await beat('b-incident', A, async () => {
    await A.go(`/p/${SLUG}/incidents`, rowOf(A, inc)) // the list as it is now, with the new incident on it
    await A.click(rowOf(A, inc), { after: 1500 })
    await A.point(A.page.locator('.page-head h2')); await sleep(3000)
    await A.point(A.page.locator('.incident-summary')); await sleep(4000)
  })
  await beat('b-studio', A, async () => {
    await A.go(`/p/${SLUG}/studio`, A.page.locator('[data-scene-item]').first())
    const panel = A.page.getByRole('region', { name: 'Also in this account' })
    await panel.waitFor({ timeout: 30_000 }); await sleep(1500)
    await A.point(panel); await sleep(4500)
  })
  return { inc }
}

async function bucketPublic(A) {
  aws('nativesense-pg', ['s3api', 'put-public-access-block', '--bucket', BUCKET, '--public-access-block-configuration',
    'BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false'])
  aws('nativesense-pg', ['s3api', 'put-bucket-policy', '--bucket', BUCKET, '--policy', JSON.stringify({
    Version: '2012-10-17', Statement: [{ Sid: 'WhiteHatsDemoPublicRead', Effect: 'Allow', Principal: '*', Action: 's3:GetObject', Resource: `arn:aws:s3:::${BUCKET}/*` }] })])
  let inc
  await wait('c-wait', A, async () => {
    await A.go(`/p/${SLUG}/incidents`)
    inc = await until(A, 'Prowler confirming the public bucket', () => incidentAbout(A, BUCKET), { every: 15_000, limit: 15 * 60_000 })
  })
  await beat('c-studio', A, async () => {
    await A.go(`/p/${SLUG}/studio`, A.page.locator('[data-scene-item]').first())
    await sleep(2500)
    const building = A.page.getByRole('button', { name: 'receipts' }).first() // a building is a button named for it
    await A.click(building, { after: 1500 })
    await A.point(A.page.getByRole('region', { name: 'Open alerts' })); await sleep(4500)
  })
  // A teammate puts it right in AWS (off camera); Priya says so, and WhiteHats checks.
  aws('nativesense-pg', ['s3api', 'delete-bucket-policy', '--bucket', BUCKET])
  aws('nativesense-pg', ['s3api', 'put-public-access-block', '--bucket', BUCKET, '--public-access-block-configuration',
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'])
  await beat('c-verify', A, async () => {
    await A.go(`/p/${SLUG}/incidents/${inc.id}`, A.page.locator('.page-head h2'))
    await A.click(A.page.locator('#change-status'), { after: 800 })
    await A.select(A.page.locator('#incident-status'), 'Fixed, please verify')
    await A.type(A.page.locator('#status-note'), 'Block Public Access is back on and the public policy is gone.')
    await A.click(A.page.getByRole('button', { name: 'Update status' }), { after: 1500 })
    await A.point(A.page.getByText('WhiteHats is checking each finding again')); await sleep(3500)
  })
  await wait('c-checking', A, async () => {
    await until(A, 'WhiteHats checking the bucket', async () => (await A.api('GET', `/incidents/${inc.id}?project=${SLUG}`)).status === 'resolved',
      { every: 10_000, limit: 10 * 60_000 })
  })
  await beat('c-resolved', A, async () => {
    await A.page.reload({ waitUntil: 'domcontentloaded' }); await sleep(2000)
    // WhiteHats resolves it either on its own check, or on seeing the undo in AWS's record first: both say why.
    await A.point(A.page.getByText('Checked again: gone.').or(A.page.getByText('Resolved by WhiteHats')).first())
    await sleep(5000)
  })
}

async function undoneInAws(A, ssh, admin) {
  aws('nativesense', ['ec2', 'revoke-security-group-ingress', '--group-id', ssh.sg, '--protocol', 'tcp', '--port', '22', '--cidr', '0.0.0.0/0'])
  // The user may be there for a reason: only what the take gave it goes, the administrator rights.
  aws('nativesense', ['iam', 'detach-user-policy', '--user-name', USER, '--policy-arn', ADMIN])
  made.sg = null
  const closed = async (inc) => CLOSED.includes((await A.api('GET', `/incidents/${inc.id}?project=${SLUG}`)).status)
  await wait('d-wait', A, async () => {
    await A.go(`/p/${SLUG}/incidents`)
    await until(A, 'both incidents closing themselves', async () => (await closed(ssh.inc)) && (await closed(admin.inc)),
      { every: 15_000, limit: 15 * 60_000 })
  })
  await beat('d-closed', A, async () => {
    await A.go(`/p/${SLUG}/incidents/${ssh.inc.id}`, A.page.locator('.page-head h2'))
    await A.point(A.page.getByText('Resolved by WhiteHats').first()); await sleep(5000)
  })
}

async function falsePositive(A) {
  const inc = await openSince(A, TITLES.image)
  if (!inc) { log('no open image-secrets incident: skipping the false positive'); return }
  await beat('e-incident', A, async () => {
    await A.go(`/p/${SLUG}/incidents/${inc.id}`, A.page.locator('.page-head h2'))
    await A.point(A.page.locator('.page-head h2')); await sleep(3000)
  })
  await beat('e-false', A, async () => {
    await A.click(A.page.getByRole('button', { name: 'False positive', exact: true }), { after: 800 })
    await A.type(A.page.locator('#false-reason'), 'These are sample values inside the operating system files, not our secrets.')
    await A.click(A.page.getByRole('button', { name: 'Close as a false positive' }), { after: 2500 })
    await A.point(A.page.locator('.page-head')); await sleep(3000)
  })
  await beat('e-precision', A, async () => {
    await A.go(`/p/${SLUG}/agents`, A.page.getByText('False positives').first())
    await A.point(A.page.getByText('False positives').first()); await sleep(4500)
  })
}

// Priya's browser keeps its profile between takes (outside the repo, beside the other demo secrets), so she signs in
// once, not on every take.
const PROFILE = path.join(os.homedir(), '.whitehats-demo', 'browser-priya')
const priyaCtx = await chromium.launchPersistentContext(PROFILE, {
  headless: false, args: ARGS, viewport: VIEW, deviceScaleFactor: SCALE, locale: 'en-US' })
await priyaCtx.addInitScript(CURSOR)
const A = await Screen.open(priyaCtx, 'priya')
let failed = null
let ssh = null
let admin = null
try {
  await A.awaitSignIn('Priya')
  await overview(A)
  ssh = await sshOpened(A)
  admin = await adminGranted(A)
  await bucketPublic(A)
  await undoneInAws(A, ssh, admin)
  await falsePositive(A)
} catch (e) {
  failed = e
  log('FAILED:', e.message)
  await A.page.screenshot({ path: path.join(OUT, 'fail-priya.png') }).catch(() => {})
} finally {
  // Whatever happened, the account is put back: SSH closed, the user without rights, the bucket private.
  const quiet = (fn) => { try { fn() } catch (e) { log('cleanup:', e.message.split('\n')[0]) } }
  if (made.sg) quiet(() => aws('nativesense', ['ec2', 'revoke-security-group-ingress', '--group-id', made.sg, '--protocol', 'tcp', '--port', '22', '--cidr', '0.0.0.0/0']))
  quiet(() => aws('nativesense', ['iam', 'detach-user-policy', '--user-name', USER, '--policy-arn', ADMIN]))
  quiet(() => aws('nativesense-pg', ['s3api', 'delete-bucket-policy', '--bucket', BUCKET]))
  quiet(() => aws('nativesense-pg', ['s3api', 'put-public-access-block', '--bucket', BUCKET, '--public-access-block-configuration',
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true']))
  await A.stopCapture()
  fs.writeFileSync(path.join(OUT, 'beats.json'), JSON.stringify(beats, null, 1))
  await priyaCtx.close()
}
if (failed) process.exit(1)
log(`done: ${beats.length} beats in ${OUT}`)
