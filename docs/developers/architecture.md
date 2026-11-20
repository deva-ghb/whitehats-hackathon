# Architecture

```
frontend/ (React)  ──>  backend/app (FastAPI, SQLAlchemy)  ──>  database (SQLite or Postgres)
                              │        │          │
                 collectors ──┘        │          └── model gateway ──> Azure OpenAI / self-hosted / rules
                 cloud log APIs <──────┘ background worker (sync every 5 min, retention hourly)
                 Fix Factory engine (../factory) <── approvals of code fixes
```

## Code map

| Path | What it does |
| --- | --- |
| `app/main.py` | App start: migrations, seed, tenant keys, background worker, help pages, static web app |
| `app/models.py` | Every table. Each row belongs to one organisation (`org_id`) |
| `app/migrate.py` | Additive schema migration on start |
| `app/security.py` | Passwords, sessions, two-step sign-in (TOTP), roles, support sessions, per-tenant secret sealing, audit |
| `app/seed.py` | Platform organisation, operator account, demo tenant, defaults for new tenants |
| `app/routers/core.py` | Sign-in, overview, Ask Big Hat |
| `app/routers/operate.py` | Incidents, signals, WhiteHats, approvals, Fix Factory bridge |
| `app/routers/telemetry.py` | Collectors, ingest endpoints, log and network analyzers |
| `app/routers/admin.py` | AI models, connectors, board report, users, audit, plans |
| `app/routers/platform.py` | Operator console, customer setup, invitations |
| `app/services/tenancy.py` | Readiness checks, go-live, first-value milestones, health, retention, export, deletion |
| `app/services/loganalyzer.py` | Shape mining, baselines, budgeted digests |
| `app/services/network.py` | Network findings from snapshots |
| `app/services/cloudlogs.py` | Cloud log syncs into the log analyzer; Azure Monitor and Google Cloud Logging pullers; identity checks |
| `app/connectors/` | The connector contract, its retry and bookmark helpers, and the connectors moved onto it (CloudWatch). See [Adding a connector](adding-a-connector.md) |
| `app/services/llm.py` | Model gateway, context packing, usage ledger |
| `app/services/notify.py` | Slack, Teams and email alerts |
| `app/services/fix_outcomes.py` | Reads the Fix Factory run behind each approved code fix and records its outcome on the fix |
| `app/whitehats/` | The WhiteHat plug-ins and their registry |
| `agent/collector.py` | The collector program deployed into customer environments |

## Tenancy

- The platform organisation (`is_platform`) holds operator accounts.
- Every other organisation is a customer tenant with a lifecycle: draft, onboarding, live, suspended, offboarded, deleted.
- Every query filters by the signed-in principal's `org_id`.
  A support session carries the customer's `org_id` and the role `support`, which no write endpoint accepts. When the customer limited the grant to one project, the session holds no role anywhere else (`permissions.roles_in`).
- Secrets are sealed with the tenant's own key; the tenant key is sealed with `WH_SECRET_KEY`.

## Data flow for a log line

1. A collector or cloud connector delivers lines.
2. `loganalyzer.ingest_lines` stores the line, finds or creates its shape, and counts it per minute.
3. `whitehats.dispatch(..., "on_log_batch")` lets each switched-on WhiteHat look at the batch.
4. When a digest is built, shapes are scored and packed into budgets; `on_log_window` runs; the summarizer model (or the rules engine) writes the summary.
5. Signals raised by WhiteHats appear on the overview and, if critical or high, go to the alerts channel.

## Life of a proposed fix

1. A WhiteHat or a person proposes a fix; it waits as `pending` for an Approver in that project or a Security lead.
2. Rejected fixes become `rejected`. Approved ones become `dispatched`.
3. A fix for another team (Slack, Okta, a runbook) is carried out by hand; a person marks it `done`.
4. A code fix is sent to Fix Factory, which proves it and then waits for a person to approve the merge there.
   Pressing **Apply Fix** on Approvals sends it.
   For a repo that takes pull requests, Fix Factory first triages the fix into pieces, one per service and one for the infrastructure, ordered code first and infrastructure last.
   Each piece is written and proven in its own sealed copy, the pieces are proven together, and each is opened as its own pull request whose CI must pass.
   Nothing is opened unless every piece is proven.
   While the pull requests wait, the fix lists them in merge order, as the engine recorded them; a person merges them on GitHub, and the engine records the merges.
   Whenever the fix, its incident or the approvals list is read, and right after a decision on the Fix Factory screen, WhiteHats reads the run.
   An approved, proven run makes the fix `verified`.
   A run that failed, was declined, could not reproduce the weakness, or whose fix was rejected or withdrawn makes it `failed`, with the reason.
   Either outcome is written once on the incident timeline, by "Fix Factory".
   If the engine cannot be reached, the fix keeps its last known state.
5. An incident can move to resolved only when none of its fixes is `pending` or `dispatched`.
