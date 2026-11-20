/**
 * What a person draws when they read the proving-ground repository (README, terraform/, shop/, gatekeeper/, worker/) and the
 * estate table in the build spec, 3.1. Written from the repository, not from the Terraform state and not from the importer:
 * P2.5 is the hand-built half of PG.3's comparison.
 *
 * A property is [label, widget, value]. Labels are the words the side panel shows; widgets are how a person fills them:
 * text (typed), list (typed, comma separated), pick (chosen from a list), yesno (chosen Yes or No).
 */

export const CLONE = 'proving-ground-by-hand'

const text = (label, value) => [label, 'text', String(value)]
const list = (label, value) => [label, 'list', value.join(', ')]
const pick = (label, value) => [label, 'pick', value]
const yesno = (label, value) => [label, 'yesno', value ? 'Yes' : 'No']

const REGION = 'ap-southeast-2'
const ACCOUNT = '509594260364'
const BUCKET = (what) => `whitehats-proving-ground-${ACCOUNT}-${what}`

const SHOP = 'Shop team'
const PAYMENTS = 'Payments team'
const PLATFORM = 'Platform team'
const SECURITY = 'Security team'

const FUNCTION = 'Serverless function'
const DYNAMO = 'Amazon DynamoDB table'
const S3 = 'Amazon S3 bucket'
const SQS = 'Amazon SQS queue'
const SSM = 'AWS Systems Manager Parameter Store'
const ROLE = 'AWS IAM role'
const LOGS = 'Amazon CloudWatch Logs'

/** The two containers: the account holds the region, the region holds every AWS node. Sizes in ground units. */
export const GROUPS = [
  { key: 'account', type: 'AWS account', name: 'Proving-ground AWS account', owner: PLATFORM,
    notes: 'The sandbox account the proving ground lives in. Everything here is tagged project whitehats-proving-ground.',
    props: [text('Account ID', ACCOUNT), pick('Purpose', 'sandbox'), list('Regions', [REGION])], at: { x: 100, y: 220 }, size: { width: 1712, height: 952 } },
  { key: 'region', type: 'AWS region', name: REGION, owner: PLATFORM, inside: 'account',
    notes: 'The only region the estate uses.',
    props: [text('Region', REGION)], at: { x: 140, y: 280 }, size: { width: 1632, height: 856 } },
]

// col and row place a node inside the region: 268 ground units across, 100 down, from its top left corner.
export const COLUMN = 268
export const ROW = 100

/** Every node. inside is the container's key; a node with none stands on the open ground, outside the account. */
export const NODES = [
  // Outside AWS: who calls the shop, and where its code and pipeline live.
  { key: 'shoppers', type: 'Customers or patients', name: 'Shoppers', owner: SHOP, open: { x: 140, y: 40 },
    notes: 'Whoever calls the shop API. The 500 customers in the table are synthetic, made from a fixed seed, and the traffic simulator stands in for them.',
    props: [text('Count estimate', 500), list('Channels', ['api']), pick('Mfa', 'off')] },
  { key: 'repo', type: 'GitHub repository', name: 'whitehats-proving-ground repository', owner: PLATFORM, open: { x: 700, y: 40 },
    notes: 'Private repository holding the shop, the gatekeeper, the worker, the Terraform and the pipeline. Main stays on the pg-baseline tag.',
    props: [text('URL', 'https://github.com/deva-ghb/whitehats-proving-ground'), pick('Auth method', 'github_app'), text('Default branch', 'main'), yesno('Branch protection', false)] },
  { key: 'workflows', type: 'GitHub Actions workflows', name: 'GitHub Actions workflows', owner: PLATFORM, open: { x: 1260, y: 40 },
    notes: 'Checks every push and deploys from main. Actions are pinned to commits. No stored cloud keys: the deploy role is assumed through OIDC.',
    props: [text('Repository', 'https://github.com/deva-ghb/whitehats-proving-ground'), pick('Cloud auth', 'oidc'), yesno('Actions pinned to sha', true)] },

  // The front door and the three functions.
  { key: 'api', type: 'Amazon API Gateway', name: 'shop-http-api', owner: SHOP, inside: 'region', col: 0, row: 2,
    notes: 'The HTTP API every caller goes through. Throttled to 10 requests a second with a burst of 20. Every route asks the gatekeeper first.',
    props: [text('API ID', 'shop-api (the id is assigned when it is deployed)'), pick('API type', 'http'), text('Region', REGION), pick('Authorizer', 'lambda'), text('Throttling rps', 10)] },
  { key: 'gatekeeper', type: FUNCTION, name: 'gatekeeper', owner: SECURITY, inside: 'region', col: 1, row: 0,
    notes: 'The only place that decides who is calling. Accepts the operator token or a signed customer token and passes the customer id on to the shop.',
    props: [pick('Platform', 'aws_lambda'), text('Runtime', 'python3.12'), pick('Package type', 'zip'), pick('Trigger', 'authorizer'), text('Memory MB', 128)] },
  { key: 'shop', type: FUNCTION, name: 'shop-api', owner: SHOP, inside: 'region', col: 1, row: 2,
    notes: 'The shop: products, orders, receipts and the payment webhook. Reads the customer id from the gatekeeper, never from a header.',
    props: [pick('Platform', 'aws_lambda'), text('Runtime', 'python3.12'), pick('Package type', 'zip'), pick('Trigger', 'http'), text('Memory MB', 256),
      list('Environment variables', ['CUSTOMERS_TABLE', 'ORDERS_TABLE', 'RECEIPTS_BUCKET', 'PAYMENT_QUEUE_URL', 'WEBHOOK_SIGNING_SECRET_PARAMETER'])] },
  { key: 'worker', type: FUNCTION, name: 'payments-worker', owner: PAYMENTS, inside: 'region', col: 1, row: 5,
    notes: 'Takes payment jobs from the queue and updates the order. Runs from a container image. Reports which messages failed so only those are retried.',
    props: [pick('Platform', 'aws_lambda'), text('Runtime', 'python3.12'), pick('Package type', 'image'), pick('Trigger', 'queue')] },

  // Data, secrets and the queue.
  { key: 'customers', type: 'Amazon DynamoDB table', name: 'customers', owner: SHOP, inside: 'region', col: 2, row: 2,
    notes: 'Synthetic customers only (500, fixed seed). Encrypted with the data key and recoverable to a point in time.',
    props: [text('Table name', 'customers'), text('Region', REGION), yesno('Point in time recovery', true), pick('Encryption', 'customer_managed')] },
  { key: 'orders', type: DYNAMO, name: 'orders', owner: SHOP, inside: 'region', col: 2, row: 3,
    notes: 'One row per order. The shop creates orders; the worker updates their status.',
    props: [text('Table name', 'orders'), text('Region', REGION), yesno('Point in time recovery', true), pick('Encryption', 'customer_managed')] },
  { key: 'receipts', type: S3, name: 'receipts', owner: SHOP, inside: 'region', col: 2, row: 4,
    notes: 'Private, encrypted and versioned. Holds a receipt per paid order, synthetic data only.',
    props: [text('Bucket name', BUCKET('receipts')), text('Region', REGION), yesno('Public access block', true), pick('Encryption', 'sse_s3'), yesno('Versioning', true)] },
  { key: 'jobs', type: 'Amazon SQS queue', name: 'payment-jobs', owner: PAYMENTS, inside: 'region', col: 2, row: 5,
    notes: 'The shop puts a job here for every paid order. After three failed tries a message moves to the dead-letter queue.',
    props: [text('Queue name', 'payment-jobs'), text('Region', REGION), text('DLQ', 'payment-jobs-dlq'), pick('Encryption', 'sse_sqs'), yesno('Fifo', false)] },
  { key: 'dead', type: SQS, name: 'payment-jobs-dlq', owner: PAYMENTS, inside: 'region', col: 2, row: 6,
    notes: 'Where payment jobs go after three failed tries, so a person can look at them.',
    props: [text('Queue name', 'payment-jobs-dlq'), text('Region', REGION), pick('Encryption', 'sse_sqs'), yesno('Fifo', false)] },
  { key: 'webhook', type: 'AWS Systems Manager Parameter Store', name: 'webhook-signing-secret', owner: SECURITY, inside: 'region', col: 3, row: 3,
    notes: 'The signing secret of the payment webhook, kept as an encrypted parameter. The shop reads it by name; the value is never in the clone.',
    props: [text('Parameter path', '/whitehats-proving-ground/webhook-signing-secret'), text('Region', REGION), pick('Type', 'secure_string'), text('Kms key', 'data key')] },
  { key: 'token', type: SSM, name: 'api-token', owner: SECURITY, inside: 'region', col: 2, row: 0,
    notes: 'The operator token the gatekeeper checks, kept as an encrypted parameter. The gatekeeper reads it by name; the value is never in the clone.',
    props: [text('Parameter path', '/whitehats-proving-ground/api-token'), text('Region', REGION), pick('Type', 'secure_string'), text('Kms key', 'data key')] },
  { key: 'key', type: 'AWS KMS key', name: 'data key', owner: PLATFORM, inside: 'region', col: 3, row: 1,
    notes: 'The one customer managed key for the shop data: both tables and both parameters. Rotates every year.',
    props: [text('Key alias', 'data key (no alias, named by its purpose)'), text('Region', REGION), yesno('Rotation enabled', true)] },

  // Logs, audit trail, image repository, artifacts.
  { key: 'shop-logs', type: 'Amazon CloudWatch Logs', name: 'shop-api log group', owner: PLATFORM, inside: 'region', col: 4, row: 2,
    notes: 'One JSON line per request. Three days of retention, set on purpose.',
    props: [list('Log groups', ['/aws/lambda/shop-api']), text('Region', REGION), list('Signals', ['requests', 'errors']), text('Retention days', 3)] },
  { key: 'gate-logs', type: LOGS, name: 'gatekeeper log group', owner: PLATFORM, inside: 'region', col: 4, row: 0,
    notes: 'Records who was let in and who was refused. Three days of retention.',
    props: [list('Log groups', ['/aws/lambda/shop-api-authorizer']), text('Region', REGION), list('Signals', ['refused callers', 'errors']), text('Retention days', 3)] },
  { key: 'worker-logs', type: LOGS, name: 'payments-worker log group', owner: PLATFORM, inside: 'region', col: 4, row: 5,
    notes: 'One JSON line per payment job. Three days of retention.',
    props: [list('Log groups', ['/aws/lambda/payments-worker']), text('Region', REGION), list('Signals', ['payment jobs', 'errors']), text('Retention days', 3)] },
  { key: 'trail', type: 'AWS CloudTrail', name: 'audit trail', owner: PLATFORM, inside: 'region', col: 4, row: 7,
    notes: 'One trail for this region only, with file validation, writing to the trail bucket.',
    props: [text('Trail name', 'whitehats-proving-ground'), yesno('Multi region', false), yesno('Log file validation', true), text('Destination bucket', BUCKET('trail'))] },
  { key: 'trail-bucket', type: S3, name: 'trail bucket', owner: PLATFORM, inside: 'region', col: 5, row: 7,
    notes: 'Holds the audit trail files. Everything in it expires after seven days.',
    props: [text('Bucket name', BUCKET('trail')), text('Region', REGION), yesno('Public access block', true), pick('Encryption', 'sse_s3')] },
  { key: 'ecr', type: 'Amazon ECR repository', name: 'payments-worker image repository', owner: PAYMENTS, inside: 'region', col: 2, row: 7,
    notes: 'Private repository for the worker image. Tags cannot be changed and the image is scanned when pushed.',
    props: [text('Repository name', 'whitehats-proving-ground/payments-worker'), text('Region', REGION), yesno('Scan on push', true), yesno('Tag immutability', true)] },
  { key: 'artifacts', type: S3, name: 'artifacts bucket', owner: PLATFORM, inside: 'region', col: 3, row: 7,
    notes: 'Holds the signed zip files of the three zip functions. Versioned.',
    props: [text('Bucket name', BUCKET('artifacts')), text('Region', REGION), yesno('Versioning', true)] },

  // Roles and the budget.
  { key: 'gate-role', type: 'AWS IAM role', name: 'pg-gatekeeper', owner: SECURITY, inside: 'region', col: 0, row: 0,
    notes: 'The gatekeeper runs as this role. It can write its own logs and read the token parameter, nothing else.',
    props: [text('Role name', 'pg-gatekeeper'), text('Permission boundary', 'pg-workload-boundary'), list('Trusted by', ['lambda.amazonaws.com'])] },
  { key: 'shop-role', type: ROLE, name: 'pg-shop-api', owner: SHOP, inside: 'region', col: 0, row: 4,
    notes: 'The shop runs as this role. It can get and put rows and receipts, send a payment job and read the webhook parameter.',
    props: [text('Role name', 'pg-shop-api'), text('Permission boundary', 'pg-workload-boundary'), list('Trusted by', ['lambda.amazonaws.com'])] },
  { key: 'worker-role', type: ROLE, name: 'pg-payments-worker', owner: PAYMENTS, inside: 'region', col: 0, row: 5,
    notes: 'The worker runs as this role. It can take jobs from the queue and update orders.',
    props: [text('Role name', 'pg-payments-worker'), text('Permission boundary', 'pg-workload-boundary'), list('Trusted by', ['lambda.amazonaws.com'])] },
  { key: 'deployer', type: ROLE, name: 'pg-deployer', owner: PLATFORM, inside: 'region', col: 5, row: 5,
    notes: 'Assumed by GitHub Actions on main through OIDC. It carries the workload boundary, so it cannot create other roles.',
    props: [text('Role name', 'pg-deployer'), text('Permission boundary', 'pg-workload-boundary'), list('Trusted by', ['GitHub Actions on main (OIDC)'])] },
  { key: 'readonly', type: ROLE, name: 'pg-whitehats-readonly', owner: SECURITY, inside: 'region', col: 5, row: 0,
    notes: 'The only access WhiteHats gets to this account: read only, plus reading the worker image so it can be scanned. It already existed before this estate.',
    props: [text('Role name', 'pg-whitehats-readonly')] },
  { key: 'budget', type: 'AWS Budgets alarm', name: 'monthly budget', owner: PLATFORM, inside: 'region', col: 5, row: 2,
    notes: 'The cost guard: 20 US dollars a month, with alerts at 50 and 100 percent. It already existed before this estate.',
    props: [text('Budget name', 'monthly budget'), text('Limit USD', 20), list('Alert thresholds', ['50%', '100%'])] },
]

// [from, kind, to]: kinds are the words the connection dialog shows. Every one is a kind the library allows between the two types.
export const EDGES = [
  ['shoppers', 'Calls', 'api'],
  ['api', 'Calls', 'gatekeeper'],
  ['api', 'Calls', 'shop'],
  ['gatekeeper', 'Runs as', 'gate-role'],
  ['shop', 'Runs as', 'shop-role'],
  ['worker', 'Runs as', 'worker-role'],
  ['gatekeeper', 'Reads', 'token'],
  ['gatekeeper', 'Reads', 'key'],
  ['shop', 'Reads', 'customers'],
  ['shop', 'Writes', 'customers'],
  ['shop', 'Reads', 'orders'],
  ['shop', 'Writes', 'orders'],
  ['shop', 'Reads', 'receipts'],
  ['shop', 'Writes', 'receipts'],
  ['shop', 'Publishes to', 'jobs'],
  ['shop', 'Reads', 'webhook'],
  ['shop', 'Reads', 'key'],
  ['worker', 'Consumes from', 'jobs'],
  ['worker', 'Reads', 'orders'],
  ['worker', 'Writes', 'orders'],
  ['worker', 'Reads', 'key'],
  ['worker', 'Reads', 'ecr'],
  ['jobs', 'Depends on', 'dead'],
  ['shop', 'Emits telemetry to', 'shop-logs'],
  ['gatekeeper', 'Emits telemetry to', 'gate-logs'],
  ['worker', 'Emits telemetry to', 'worker-logs'],
  ['trail', 'Writes', 'trail-bucket'],
  ['repo', 'Built by', 'workflows'],
  ['workflows', 'Pushes image to', 'ecr'],
  ['workflows', 'Authenticates via', 'deployer'],
  ['workflows', 'Writes', 'artifacts'],
  ['shop', 'Deploys from', 'repo'],
  ['gatekeeper', 'Deploys from', 'repo'],
  ['worker', 'Deploys from', 'repo'],
  ['customers', 'Depends on', 'key'],
  ['orders', 'Depends on', 'key'],
  ['webhook', 'Depends on', 'key'],
  ['token', 'Depends on', 'key'],
]
