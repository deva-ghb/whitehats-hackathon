/** Labels and one-line consequences for the shared Select. Prices match the plan catalogue. */

export const PLANS = [
  { value: 'starter', label: 'Starter', description: '$1,500 / month. 5 seats and 10 collectors.' },
  { value: 'growth', label: 'Growth', description: '$4,900 / month. 20 seats and 100 collectors.' },
  { value: 'enterprise', label: 'Enterprise', description: 'Talk to us. 500 seats and 5,000 collectors.' },
]

export const ROLES = [
  { value: 'ceo', label: 'CEO', description: 'Account owner and Approver: runs the account, its people and connections, and approves fixes.' },
  { value: 'ciso', label: 'CISO', description: 'Security lead: triages and approves fixes in every project. Cannot manage people.' },
  { value: 'analyst', label: 'Analyst', description: 'Engineer in every project: investigates and edits. Cannot approve fixes.' },
  { value: 'engineer', label: 'Engineer', description: 'Engineer in the projects they are added to. Cannot approve fixes.' },
]

const REGION_META = {
  'in-south': { group: 'India', description: 'Company data is stored in Hyderabad.' },
  'in-west': { group: 'India', description: 'Company data is stored in Mumbai.' },
  'eu-west': { group: 'Europe', description: 'Company data is stored in Ireland.' },
  'us-east': { group: 'United States', description: 'Company data is stored in Virginia.' },
}

const PROVIDER_META = {
  azure_openai: { description: 'You will need an endpoint, a deployment name and an API key.', icon: 'brain' },
  vertex: { description: 'Use a Gemini model ID and Vertex AI API key.', icon: 'brain' },
  openai_compatible: { description: 'You will need a base URL and an API key.', icon: 'plug' },
  claude_cli: { description: 'You will need the Claude command on this server. No key is stored here.', icon: 'spark' },
  rules: { description: 'No model call. The built-in rules write the answer.', icon: 'book' },
}

export const ALERT_CHANNELS = [
  { value: 'slack', label: 'Slack', description: 'An incoming webhook. Critical and high signals are posted there.', icon: 'chat' },
  { value: 'teams', label: 'Microsoft Teams', description: 'A workflow webhook. Critical and high signals are posted there.', icon: 'people' },
  { value: 'email', label: 'Email', description: 'A team address. Alerts are sent as email.', icon: 'report' },
]

export const SUPPORT_HOURS = [1, 4, 24, 72].map((hours) => ({
  value: hours,
  label: `${hours} hour${hours > 1 ? 's' : ''}`,
  description: 'Read-only access. It ends when the time is up.',
}))

export const WORK_ZONES = [
  { value: 'UTC', label: 'UTC', description: 'The default. Sign-ins are judged in UTC.', group: 'Universal' },
  { value: '+05:30', label: 'India (+05:30)', description: 'India Standard Time. No daylight saving.', group: 'Asia' },
  { value: 'Asia/Kolkata', label: 'Kolkata', description: 'India, as a named zone.', group: 'Asia' },
  { value: 'Asia/Singapore', label: 'Singapore', description: 'Singapore, UTC+8.', group: 'Asia' },
  { value: 'Asia/Tokyo', label: 'Tokyo', description: 'Japan, UTC+9.', group: 'Asia' },
  { value: 'Australia/Sydney', label: 'Sydney', description: 'Australia east, with daylight saving.', group: 'Pacific' },
  { value: 'Europe/London', label: 'London', description: 'United Kingdom, with daylight saving.', group: 'Europe' },
  { value: 'Europe/Berlin', label: 'Berlin', description: 'Central Europe, with daylight saving.', group: 'Europe' },
  { value: 'America/New_York', label: 'New York', description: 'US east, with daylight saving.', group: 'Americas' },
  { value: 'America/Los_Angeles', label: 'Los Angeles', description: 'US west, with daylight saving.', group: 'Americas' },
]

export const WORK_TIMES = Array.from({ length: 24 }, (_, hour) => {
  const label = `${String(hour).padStart(2, '0')}:00`
  return { value: label, label, description: 'A time in the company zone, on a 24-hour clock.' }
})

export const LOG_WINDOWS = [
  { value: 15, label: 'Last 15 min', description: 'The digest looks back 15 minutes.' },
  { value: 60, label: 'Last 1 hour', description: 'The digest looks back 1 hour.' },
  { value: 360, label: 'Last 6 hours', description: 'The digest looks back 6 hours.' },
  { value: 1440, label: 'Last 24 hours', description: 'The digest looks back 24 hours.' },
]

// What a person may set (FD.2): resolved is WhiteHats' to say after checking again; false positive is an approver's.
export const NEXT_STATUS = {
  open: ['investigating', 'verifying'],
  investigating: ['verifying'],
  verifying: [],
  resolved: [],
  false_positive: [],
}

export const INCIDENT_STATUSES = [
  { value: 'open', label: 'Open', description: 'Seen, and not being worked yet.' },
  { value: 'investigating', label: 'Investigating', description: 'Someone is working out what happened.' },
  { value: 'verifying', label: 'Fixed, please verify', description: 'WhiteHats checks each finding again, then resolves it or sends it back.' },
  { value: 'resolved', label: 'Resolved', description: 'WhiteHats checked: the problem is gone.' },
  { value: 'false_positive', label: 'False positive', description: 'An approver said it was never a real problem.' },
]

export const roleLabel = (value) => ROLES.find((role) => role.value === value)?.label || value

// Spec 0A roles. A project role holds only inside the project it was given in.
export const PROJECT_ROLES = [
  { value: 'project_admin', label: 'Project admin' },
  { value: 'approver', label: 'Approver' },
  { value: 'engineer', label: 'Engineer' },
  { value: 'viewer', label: 'Viewer' },
]
export const PROJECT_ROLE_LABEL = Object.fromEntries(PROJECT_ROLES.map((role) => [role.value, role.label]))
const ACCOUNT_ROLE_LABEL = {
  account_owner: 'Account owner', account_admin: 'Account admin', security_lead: 'Security lead', auditor: 'Auditor',
}

/** What a person may do, one line each: their account role, then each project role and where it holds. */
export function accessLines(person) {
  const lines = person.account_role ? [ACCOUNT_ROLE_LABEL[person.account_role] || person.account_role] : []
  const byRole = new Map()
  for (const grant of person.projects || []) byRole.set(grant.role, [...(byRole.get(grant.role) || []), grant.project])
  for (const [role, projects] of byRole) {
    const where = projects.length > 1 && projects.length === person.projects_shown ? 'every project' : projects.join(', ')
    lines.push(`${PROJECT_ROLE_LABEL[role] || role} in ${where}`)
  }
  return lines.length ? lines : ['No project yet']
}

export function regionOptions(regions) {
  return Object.entries(regions || {}).map(([value, label]) => ({
    value,
    label,
    description: 'Company data is stored in this region.',
    ...(REGION_META[value] || {}),
  }))
}

export function providerOptions(providers) {
  return Object.entries(providers || {}).map(([value, label]) => ({
    value,
    label,
    ...(PROVIDER_META[value] || {}),
  }))
}
