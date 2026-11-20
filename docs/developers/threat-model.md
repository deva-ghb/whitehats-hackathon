# Threat model of WhiteHats itself

WhiteHats holds a map of each customer's estate and read access to their systems. This page names what is worth stealing, who would want it, and the control that stands in the way. A control that is not built yet is marked as a gap, with the task that owns it.

## Who we are defending against

- Someone already inside one customer, looking for a path into the other customers.
- A stolen operator session, laptop or backup.
- A dependency that was published in order to be installed here.
- Text inside a log line or a source file, written to steer the confirming model.

## Assets

| Asset | Who could want it | Control |
| --- | --- | --- |
| The estate map: clones, incidents, signals, findings | A thief planning a job, or a rival | Every row carries `org_id`, and every query uses the signed-in company. The support role cannot write. Gap: one database file holds every company, so a stolen disk is all of them until clone data is encrypted per company (P3.4). |
| Sealed connector secrets, vault credentials and each company's key | Someone who wants to read a customer's cloud | Each company has its own key. That key is wrapped with `WH_SECRET_KEY`, which stays in the environment and is not in the repo. Vault credentials are sealed with that same key. A clone, an API response, a log line and an export carry the reference only. Unwrapping one company's key opens only that company's secrets. Unwrapping `WH_SECRET_KEY` opens every company, so that key is the crown jewel. |
| Cloud read access | Someone who wants the customer's logs without touching the customer | CloudWatch is read by assuming a role. The session is short-lived. A stored access key is refused. Gap: Azure Monitor and Google Cloud Logging still take a stored secret. Those should become short-lived tokens before a design partner. |
| Prompts, evidence and model answers | Someone who wants a real finding dismissed, or the text that was sent | The gateway sends a short summary, not raw tables, inside a token budget. Gap: the confirmation step must treat evidence as data and must not lower a high result (VS.3). |
| Sessions and the operator role | Someone who wants to act as the customer | Passwords are hashed with PBKDF2. The operator must complete a second step, and a company can require it for everyone. A session is a token signed with the session signing key and lasts `WH_TOKEN_HOURS` (12 by default). Nothing is stored per session, so every request re-checks that the person is still active and the company is not suspended. Gap: one stolen session cannot be ended on its own before it expires; deactivating the person ends all of theirs, and rotating the signing key ends everyone's. Collector and invitation tokens are different: they are random and stored only as a hash. |
| The session signing key (`WH_JWT_SECRET`, or the file `jwt.key`) | Someone who wants to sign in as anyone, in any company, without a password or second step | Anyone holding this key can make a valid session for any person, including the operator, so it is a crown jewel next to `WH_SECRET_KEY`. In production it comes from a secret manager. See [Keys](#keys). |
| Fixes and the Runner | Someone who wants a bad change shipped, or a tampered Runner | A person approves a fix before it is applied. Gap: release images are not signed yet, and the Runner does not yet check a signature before it updates (P4.1). |
| Our own dependencies | Someone who published a package for us to install | `uv.lock` and `package-lock.json` pin the versions the suites install. A bump should be at least 48 hours old unless it is a security fix. The Supply chain WhiteHat that enforces this on a customer is H5, and the same rule applies to this repo. |

## Keys

WhiteHats has two keys that open every company: the master key `WH_SECRET_KEY`, which unwraps each company's own key, and the session signing key `WH_JWT_SECRET`.
In production both come from a secret manager as environment variables.
If either is not set, WhiteHats generates it on first start and keeps it in `WH_DATA_DIR` as `secretbox.key` or `jwt.key`.
The file is created readable by its owner only, in one step, so it is never briefly open to other users.
That fallback is for a laptop or a pilot: the keys then sit next to the SQLite database, so a copy of the data folder holds everything.
Back up the keys separately from the database (see [Backups and upgrades](../operations/backups-and-upgrades.md)).

**Rotating the session signing key.**
Set a new `WH_JWT_SECRET` (or delete `jwt.key`) and restart.
Every session and every half-finished sign-in becomes invalid at once, and people sign in again with their password and second step.
Nothing else is signed with this key, so no stored data needs to change.
Rotate it at once if it may have leaked, when someone who could read it leaves, and otherwise every 90 days.
`test_rotating_the_signing_key_signs_everyone_out` proves the old sessions stop working.

**Rotating the master key.**
There is no tool for this yet: changing `WH_SECRET_KEY` alone makes every stored connector secret unreadable.
Gap: a rotation that re-wraps each company's key under the new master key belongs with per-company encryption (P3.4).
Until then, treat a leak of `WH_SECRET_KEY` as a leak of every stored connector secret, and rotate those secrets at their source.

## What this page does not claim

A penetration test of WhiteHats itself has not been done. F.1 cannot pass until an independent test has been done and its high findings are fixed.
