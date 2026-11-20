# API

The interactive reference is at `/docs` on any running server (FastAPI's OpenAPI viewer).

## Authentication

1. `POST /api/auth/login` with email and password.
   The answer's `step` is `done` (with a `token`), `mfa` or `mfa_setup` (with a short-lived `step_token`).
2. For `mfa`: `POST /api/auth/mfa` with the step token and the 6-digit code.
3. For `mfa_setup`: `POST /api/auth/mfa/setup` returns a secret and QR code; `POST /api/auth/mfa/confirm` with a code finishes.
4. Send `Authorization: Bearer <token>` on every other call. Sessions last 12 hours.

Collectors use their own token on `/api/ingest/*` only.

## Who may call what

Every route checks one permission from the spec 0A role matrix (`backend/app/permissions.py`, and `decisions/permission-matrix.md` for the reasoning).
A refusal is `403` and says which roles may do it.
A route about one project acts on the project named by the `project` query parameter (its slug), or on Main; a route about one row (an incident, a fix, a clone) acts on that row's project.
A row in another company answers `404`.
`GET /api/auth/me` returns `user.permissions`, the permissions the person holds, so a screen can hide or explain a control.
Add `?project=<slug>` to read the permissions for that project. Without it, the project permissions are Main's.

## What a project read returns

A read about a project answers with that project's rows only.
The Overview, its briefing (`GET /api/overview/summary`), Ask Big Hat (`/api/assistant/*`), the WhiteHats list and its summary (`GET /api/agents`), the network findings and the board report all work this way.
The briefing is cached per project, and a conversation with Big Hat belongs to the project it was held in.

Raw logs, network connections and what a collector sent come from the account's collectors and belong to no project yet.
They need `read_account_data`, which only the roles that see every project hold: Account owner, Account admin, Security lead and Auditor.
That covers `GET /api/collectors/{id}`, `GET /api/logs/overview`, `/api/logs/digests`, `/api/logs/templates/{id}` and `/api/logs/search`.
`POST /api/logs/digest` also needs `triage`, so a Security lead can build one and an Auditor cannot.
`GET /api/network/overview` gives everyone in the project its findings; the connection summary and flows are `null` and empty for anyone else, with `raw_hidden` saying why.
The collector list (`GET /api/collectors`) stays shared setup, like the connections list.
A briefing includes the latest log digest only for the same four roles.

Fix Factory works on the code of the Main project.
In any other project `GET /api/factory/status` answers `{"connected": false, "elsewhere": true, "message": ...}` and the other Fix Factory routes answer `409`.
A run that a fix in another project started answers `404` in Main, and approving a Fix Factory fix outside Main answers `409`.
A run whose fix was opened as pull requests is approved by merging them on GitHub: `POST /api/factory/runs/<id>/approve` answers `409` naming the pull requests in order, and `.../reject` closes them.

`GET /api/reports/board?account=true` covers only the projects this person can open.

The help pages (`/api/help`) need a session. `GET /api/health` and the API description stay open.

## Connections and projects

- `GET /api/connectors/{id}/projects` lists the projects a connection feeds and each scope, and says when it is account-wide.
- `PUT /api/connectors/{id}/scope?project=<slug>` with `{"scope": {...}}` attaches the connection to that project, or changes its scope. A filter the connection's kind cannot use is refused with `400`.
- `DELETE /api/connectors/{id}/scope?project=<slug>` detaches it: new data stops reaching that project, and what it already holds stays.

Both changes need `attach_connections` in that project (Project admin or Account admin) and are written to the audit trail.

## Connections as nodes on the canvas

A connection is a node on the canvas (STUDIO.4): a container of a `connection.<name>` library type (`connection.aws`, `connection.github`, `connection.azure-monitor`, and one for each connector), with `properties.connector` naming the connector and nothing else. The role, the external id and every secret stay with the connection on the server and never go into a clone. There is no Connectors screen: an old `/connectors` link opens the Studio with that connection's node selected (`/studio?connection=<name>`).

- `POST /api/clones/{id}/connections/{key}/bring-in` saves what the connection found, as this project's scope lets it, into the clone inside the connection's node. It needs `edit_model` or `manage_connections` (STUDIO.7: bringing in is the connection drawing, not a person). It is the ordinary import (`/api/imports`) narrowed to one connection: what sits at the top of what it found goes inside the node, a resource the connection no longer finds goes away, and what a person drew by hand is never touched. It returns `added`, `updated`, `removed`, `held` (items waiting for a library type, left in the change set) and the clone `version`. A clone with no node for the connector is refused with `422`.
- `POST /api/connections/{key}/place` puts the connection's node on the project's map, starting the map (named after the project) when the project has none. It needs `edit_model` or `manage_connections`. It returns `clone`, `node`, `version` and `placed` (false when the node was already there). The node's provenance is `discovered, from WhiteHats`, so a bring-in never removes it.
- `GET /api/connections/{key}/choices` lists the regions and repositories the connection has found and what this project is narrowed to, so a person picks from real ones. It needs `see_project`.

Setting a connection up, checking it, finding resources and choosing what a project reads are the connector routes above (`PUT /api/connectors/{id}`, `POST /api/connectors/{id}/check`, `POST /api/connectors/{id}/discover`, `PUT /api/connectors/{id}/scope`). Setup is an account right (`manage_connections`), choosing what a project reads is a project right (`attach_connections`), and bringing findings into a clone is an editing right (`edit_model`); no built-in role holds the first and the last, so one person holds both through an account role and a project role.

When the GitHub App is installed and the server has `GITHUB_WEBHOOK_SECRET`, a bring-in of the GitHub connection also places a `GitHub push listener` node (library type `delivery.github-webhook`) inside it. Without the secret nothing is listening, so no node says so.

## Projects

- `GET /api/projects` lists the projects this person can open, each with its score, criticality, open findings, connection health and last activity.
- `GET /api/account/overview` is the account roll-up for those same projects: each project's score, the account score (high counts three times, medium twice, low once), the lowest project, and findings counted once when the same resource is in more than one project.
  A finding is an open signal: one not on a resolved incident. The same real resource with the same title counts once for the account, however many projects hold it; each project still counts its own.
  Each project's posture is scored and stored on its own every day, so a project's history is that project's, and a finding shared by two projects lowers the weighted account score once.
- `GET /api/reports/board` is one project's report. `?account=true` is the account report, with the weighted score and the lowest project.
- `POST /api/projects` opens one. `kind` is `empty`, `template` (`health` or `fintech`) or `connection` (a connector id and a scope). Needs `manage_projects`. The creator becomes Project admin of it.
- `GET /api/projects/<slug>/settings?project=<slug>` is the name, criticality, environments, two-approver flag, members and attached connections.
- `PATCH /api/projects/<slug>` changes the name, criticality, environments or archives it. Needs `manage_projects`.
- `PATCH /api/projects/<slug>/policy?project=<slug>` with `{"two_approver": true}` needs `set_policies`.
- `POST /api/projects/<slug>/archive?project=<slug>` with `{"confirm": true}` needs `archive_project`.
- `POST /api/projects/<slug>/members?project=<slug>` with an email, a project role and a `reason` needs `manage_members`. The person must already be in the account. The reason is required (at least 5 characters) and goes on the audit trail.
- `DELETE /api/projects/<slug>/members?project=<slug>` with an email and a `reason` takes someone out of the project, also with `manage_members`. Their account and other projects stay.
- Nobody grants, changes or removes their own project role: those calls answer `403`.
- An archived project is read-only for account roles and closed to project roles (R-047, Deva 2026-10-30). An account role keeps only "see the project" and "read the project audit trail" in it, and any other project route answers `403` ("This project is archived, so it is read-only."); a project role gets `404` ("This project is archived.") on every project route. Its WhiteHats take no new data. `GET /api/projects/archived` lists archived projects for the account roles (empty for anyone else), and `GET /api/auth/me?project=<slug>` says `project_archived`. `PATCH /api/projects/<slug>` with `{"archived": false}` (an Account owner or Account admin) restores it.

## Role rules the server enforces

- Approving: in a project with `two_approver` on, a fix whose risk is `critical` needs two different Approvers. The first `POST /api/remediations/{id}/approve` records `first_approved_by` and leaves the fix `pending`; the same person approving again answers `409`; a second, different Approver dispatches it, and `decided_by` names both.
- Only an Account owner can make an Account owner: an Account admin inviting with role `ceo`, or changing someone to it, answers `403`.
- Settings every project uses need `manage_account_setup` (Account owner or Account admin): `PUT /api/setup/hours` and `POST /api/factory/practice/reset`.

## Audit and support by project

- `GET /api/admin/audit` is the whole account's trail, for the account roles.
- `GET /api/projects/audit?project=<slug>` is one project's trail: the actions taken inside it. A Project admin reads their own project's; the account roles read any.
- `POST /api/setup/support-access` takes an optional `project` slug. A support session is then refused everywhere outside that project, and it still ends when the grant expires.

## Main groups

| Prefix | For |
| --- | --- |
| `/api/overview`, `/api/assistant` | The CEO view and Ask Big Hat |
| `/api/incidents`, `/api/signals`, `/api/remediations`, `/api/agents` | Responding |
| `/api/factory/*` | Fix Factory bridge |
| `/api/library/search` | Node library search: `q`, the canvas `cloud`, `inside` and `from` for ranking, and `family`, `provider`, `vendor`, `importable`, `whitehat_mode` to filter. Any role in the account. |
| `/api/collectors`, `/api/ingest/*`, `/api/logs/*`, `/api/network/*` | Visibility |
| `/api/models`, `/api/connectors`, `/api/reports/board`, `/api/admin/*` | Platform settings |
| `/api/setup/*` | The customer's onboarding checklist |
| `/api/clones`, `/api/clones/*` | Clone Studio: list a project's clones, read, version, import and export them. A save sends a `reason` and the document; the author is always the signed-in person. The version list carries each version's author, reason and time. Unless `WH_STUDIO_HAND_EDITING=1`, creating, importing and saving a clone by hand is refused with `409` (STUDIO.7) |
| `/api/imports`, `/api/imports/*` | Imports (PG.3): start one from a Terraform state, from what the project's connections found, or from the repositories its GitHub connection reaches (`repository`), read it, decide each change, apply it, discard it. Starting, deciding, applying and discarding need `edit_model`; reading needs `see_project`. Body of `POST /api/imports`: `source` (`terraform_state`, `connections` or `repository`), exactly one of `clone_id` and `new_clone` (`name`, `environment`; started only once the source has been read), and `state` for a Terraform state. `PUT /api/imports/{id}/decisions` takes `keys`, `decision` (`accept`, `reject`, `undecided`) and, for a held item, `type`. A held item is accepted only with a library type that is not Generic. `GET /api/imports/sources` (`see_project`) says whether a repository scan can start for this project and, if not, why in words; it makes no call to GitHub. |
| `/api/library/types` | Every node library type, for the Studio's library rail and inspector |
| `/api/operator/*` | The WhiteHats operator console |
| `/api/invites/*` | Accepting an invitation (no sign-in) |
| `/api/help` | These docs (signed in) |
