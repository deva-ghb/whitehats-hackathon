# Onboarding a company

This runbook takes a new customer from signed contract to live service.
The operator console enforces it: a company cannot go live until every required check is green or waived with a reason, and both sides have signed off.

Sign in at the platform address with your operator account.
Operators always use two-step sign-in.

## The flow at a glance

1. **Create the company** in the operator console (*Companies → New company*). It starts as a *draft*.
2. **Tick the contract checks** as the paperwork arrives.
3. **Send the invitation** to the customer's first admin. The company moves to *onboarding*.
4. **The customer completes their checks** from their own *Setup* screen: domain, connectors, collectors, alerts, AI model.
5. **Press Re-check**, then **Sign off go-live**. When the customer also signs off, the company is *live*.
6. **Watch time to first value** for the first 30 days on the company's page.

Every action you take is written to both the platform audit trail and the customer's audit trail.

After sign-in the customer lands on the account home, which lists every project they can open. Inside a project the address is `/p/<slug>/...`. Project settings hold the name, criticality, environments, attached connections, members and the option that two people must approve a fix. Account settings, reached from that same bar, hold the people and account roles, and link to connections, the Runner and AI models. A control someone may not use is disabled and says why.

## Stage 1: contract and legal

### Signed contract {#contract}

Manual check.
Confirm a signed order form exists with the plan, the start date and the price.
Record the order reference in the company's contract details.
Do not create users or collect data before this is ticked.

### Data processing agreement {#dpa}

Manual check.
The customer must sign our data processing agreement, including the list of outside services that may see their data.
That list must name the AI model providers allowed by their AI policy (see [AI policy](#ai-policy)).
If they refuse all AI providers, set the policy to *Rules engine only*.

### Pilot pass marks {#pass-marks}

Manual check, required for pilots.
Write the pass marks into the contract details in plain words, for example "3 proven fixes and a weekly board report within 30 days".
A pilot without written pass marks never ends in a decision.

### Billing {#billing}

Manual check, optional.
Record the billing contact and the purchase order number if the customer uses one.

### Contacts {#contacts}

Automatic check.
Passes when the contract details name the customer owner (with an email) and a security contact who can be reached in an emergency.

## Stage 2: security and compliance

### Region {#region}

Automatic check.
Each installation stores data in one region, set by `WH_REGION` on the server.
The check passes only when the company's region matches the installation's region.
If a customer needs another region, onboard them on an installation in that region; never store their data elsewhere.
Indian customers default to `in-south` (Hyderabad) because of India's Digital Personal Data Protection Act.

### Retention {#retention}

Automatic check.
Set how long raw log lines, run records and the audit trail are kept.
Allowed ranges: logs 7 to 365 days, runs 30 to 2,555 days, audit trail at least 365 days.
The background worker deletes data past its retention every hour.

### AI policy {#ai-policy}

Automatic check.
Agree which AI providers may read summaries of the customer's data, and whether zero data retention is required.
The model gateway enforces this: a model whose provider is not allowed, or that does not offer zero retention when required, is never called for this company.
Raw logs never reach any model; only budgeted summaries do.

### Domain verification {#domain}

Automatic check, completed by the customer.
Enter the customer's email domain when creating the company.
The console shows a DNS TXT record such as `whitehats-verify=3f9a…`.
The customer adds it to their domain's DNS; then press *Re-check*.
Only addresses at a verified domain can be invited.
If the domain genuinely cannot carry DNS records, waive the check with a reason.

### Two-step sign-in {#mfa}

Automatic check.
Passes when two-step sign-in (an authenticator app code) is required for every user, or single sign-on is enforced.
Leave it on.

### Support access {#support-access}

Automatic check.
WhiteHats staff see nothing inside a company by default.
The customer can grant time-limited support access (up to 72 hours) from their *Setup* screen, with a reason.
The grant can be limited to one project; the session then sees nothing outside it, not even the account's shared pages.
While granted, an operator can open a **read-only** support session from the company's page.
Every support session is written to the customer's audit trail.
The check shows as pending while access is granted, and blocks go-live, so a company cannot go live with standing access.

### Encryption {#encryption}

Automatic check.
Each company gets its own encryption key when it is created.
Their stored secrets (connector credentials, model keys, alert webhooks) are sealed with it, and the key itself is sealed with the installation's master key (`WH_SECRET_KEY`).

## Stage 3: first admin

### Invitation {#invite}

From the company's page, *Invite first admin*.
Use an address at the company's domain and choose the title, CEO or CISO. Either way this first admin becomes the Account owner and an Approver in every project, so they can invite everyone else.
The invitation link works once and expires after 72 hours.
If SMTP is configured (`WH_SMTP_HOST`), it is emailed; otherwise copy the link and send it through a channel the customer trusts.
WhiteHats never sees or sets the customer's password.

### First admin ready {#first-admin}

Automatic check.
Passes when an admin has accepted the invitation, turned on two-step sign-in and accepted the terms.
From this point the customer controls their own company.
Their admins invite colleagues under *Settings → People*, and those invitations follow the same rules as yours: an address at the company's domain, a one-time link that expires after 72 hours, and a password only the invited person knows.
Open invitations count against the plan's seats, so a company cannot invite past its limit.
The operator console shows who sent each invitation.

## Stage 4: technical readiness

The customer does most of this from their *Setup* screen, which links to the right guide for each item.

| Check | Passes when | Guide |
| --- | --- | --- |
| Identity connector | Okta, Entra ID or Google Workspace verified | [Okta](../connectors/okta.md), [Entra ID](../connectors/entra-id.md), [Google](../connectors/google-workspace.md) |
| Cloud logs (Growth and Enterprise) | A cloud log source synced in the last hour | [CloudWatch](../connectors/aws-cloudwatch.md), [Azure Monitor](../connectors/azure-monitor.md), [Google Cloud Logging](../connectors/gcp-logging.md) |
| Collector reporting | At least one collector online, or a cloud source syncing | [Collectors](../deploy/collectors.md) |
| WhiteHats | Every contracted WhiteHat is switched on | [Adding a WhiteHat](../developers/adding-a-whitehat.md) |
| AI model | An allowed model passed its test, or the policy is rules-only | [AI models](ai-models.md) |
| Alerts | A test message was delivered | [Alerts](../connectors/alerts.md) |
| Repositories (optional) | Every linked repository answers `git ls-remote` | [GitHub](../connectors/github.md), [Fix Factory](#fix-factory) |
| Plan limits | Seats and collectors within the plan | [Plans](#plans) |

### Fix Factory {#fix-factory}

A Fix Factory engine works on one company's repositories and has no notion of companies, so each engine belongs to exactly one company.
The installation setting `WH_FACTORY_TENANT` names that company by its short name (the demo company, `acme`, by default).
Every other company sees "Fix Factory is not set up for your company yet" and never sees another company's runs or fixes.
To give a new company proven fixes, run a separate engine for it and point a separate installation, or a future per-company engine setting, at it.

### Plan limits {#plans}

Starter allows 5 seats and 10 collectors, Growth 20 and 100, Enterprise 500 and 5,000.
The plan is part of the contract, so customers cannot switch it themselves.
Their Account owner can request a change from *Settings → Plan and usage*; the request appears in *Platform audit*.
Agree the new price, then change the plan from the company's *Settings* tab.

## Stage 5: go-live {#go-live}

Press *Re-check* to run the slow checks (DNS and repository reachability).
When nothing blocks, press *Sign off go-live*.
The customer's Account owner or Account admin signs off from their *Setup* screen.
The company becomes *live* when both have signed, in either order.

## After go-live: time to first value

The company's page tracks seven milestones against target days from creation:

| Milestone | Target |
| --- | --- |
| First data received | Day 1 |
| First posture score | Day 1 |
| First log digest | Day 3 |
| First network snapshot | Day 3 |
| First approval decision | Day 7 |
| First board report | Day 7 |
| First proven fix approved | Day 14 |

A milestone shows as *slipping* on its due day and *overdue* two days later.
An overdue milestone is a customer success call, today.

Until any data has arrived from a collector or cloud log source, the company's posture score carries a 400-point "Blind spots" penalty.
A quiet dashboard for a company we cannot see means "unseen", not "safe".

## Suspending a company

Use *Suspend* for unpaid invoices or a security concern, with a reason.
Users cannot sign in and collectors are refused until you *Resume*.
Nothing is deleted.
