# Permission matrix

**Status:** accepted
**Date:** 2026-10-23

Access follows the role matrix of spec section 0A, written as data in `backend/app/permissions.py`.
Every route declares what it needs with `allow(permission, project_from=...)`, and nothing else decides access.
Access is denied unless a role grants it.

The alternatives were to keep the four job-title roles (CEO, CISO, analyst, engineer) with a project check added on top, or to keep both systems side by side until the project screens arrive.
Both would leave two answers to "may this person do this", so the matrix replaced the old role lists at once.
The old `require(...)` helper and its role lists are gone.

Choices made where the spec table is silent:

- Six permissions are not rows of the spec table (`read_account_data` and `manage_account_setup` are described below): `use_account` (see the account's shared pages: people, models, connections, setup status), `sign_off` (go-live sign-off and support access, for the account owner and admin), `be_signed_in` (read your own session) and `operate_platform` (WhiteHats staff).
- Each existing route was mapped to the nearest row: working hours and switching WhiteHats on are WhiteHat settings (`edit_model`); alert channels, code repositories, collectors and connector syncs are connections (`manage_connections`); opening an incident, marking a manual fix done, starting a Fix Factory run and building a log digest are triage; approving, rejecting and deciding a Fix Factory run are `approve`.
- Job titles still exist as the label people are invited with. Changing a title moves the account role and project memberships the title gave (`projects.change_legacy_role`).
- The operator's invitation of a company's first admin makes an Account owner, who is also Approver in every project, as the CEO mapping does. Otherwise a new account would have nobody who could invite anyone.
- Audit: an action taken inside a project is filed under it (`audit()` reads the project `allow()` resolved). The four account roles read every project's trail and the whole account's; a Project admin reads only their project's, through `GET /api/projects/audit`.
- A support session the account limits to one project gets the role `support_elsewhere` outside it, which allows only reading its own session.
- `read_account_data` (gap): raw logs, network flows and what a collector sent come from the account's collectors and are not tied to one project until AP.3 routes them. Only the roles that see every project (Account owner, Account admin, Security lead, Auditor) read them. Building a log digest also needs `triage`. The collector list stays `use_account`, because it is setup, like the connections list.
- Fix Factory's engine works on one company's code, which belongs to the Main project until projects name their own repositories (CN.1). Its routes answer only in Main, and a run a fix in another project started is that project's.
- A summary read (briefing, Ask Big Hat, board report, WhiteHats summary, posture) is limited to the project the request names; the account board report is limited to the projects the person can open.
- The help pages need a session (`be_signed_in`), because they describe how the platform is built and run. The route scan walks the whole app, so a route added in `main.py` is checked too.
- `manage_account_setup` (gap, R-041): working hours and the Fix Factory practice repository are the account's and every project uses them, so a project role cannot change them. Account owner and Account admin hold it. Working hours were `edit_model` before; an Engineer no longer sets them.
- R-041 rules beyond the table: two different Approvers for a critical fix when the project asks; nobody grants, changes or removes their own project role; every project grant and removal carries a reason on the audit trail; only an Account owner makes an Account owner; an archived project is read-only for account roles (see the project, read its audit trail) and closed to project roles, until an account role restores it (R-047, Deva 2026-10-30).
- Until URLs carry a project (AP.4), a request acts on the project named by the `project` query parameter (a slug), or on Main.
- `/api/auth/me` returns the permissions the person holds in Main and across the account. Screens use it to hide or explain a control; the server checks again on every call.

Consequences, as the spec intends: the demo CEO no longer edits WhiteHat settings or clones, and the CISO no longer manages people or connections.
Overview, the briefing and the board report read one project (AP.2 reopened, review 7); the account roll-up is AP.5.
