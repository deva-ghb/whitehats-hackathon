# Backups and upgrades

## What to back up

1. **The database.** Postgres: `pg_dump` nightly, kept 30 days. SQLite: copy `whitehats.db` while the service is stopped, or use `sqlite3 whitehats.db ".backup backup.db"`.
2. **The keys.** `WH_SECRET_KEY` and `WH_JWT_SECRET`, or the files `secretbox.key` and `jwt.key` in `WH_DATA_DIR`. Without the secret key, every stored credential is unreadable.
   Keep the key backup in a different place from the database backup: someone holding both can read every company's secrets and sign in as anyone.
   How to rotate the keys is in the [threat model](../developers/threat-model.md#keys).
3. **Exports.** `WH_DATA_DIR/exports`, until each customer confirms receipt.

Test a restore every quarter: start a copy with the restored database and keys, and sign in.

## Upgrading

1. Back up the database and keys.
2. Deploy the new code and run `uv sync`.
3. Restart the service.

On start, `app/migrate.py` creates new tables, adds new columns, and drops tables of retired features (listed in `RETIRED_TABLES`).
List and settings columns that come back empty on old rows are filled with an empty list or settings object, because a database cannot express those defaults when it adds a column.
Data steps for older rows live in `_upgrade_rows`; each one only touches rows that still need it, so restarting is always safe.
A step that must never repeat, because a later row could look like an old one, goes in `_ONCE_STEPS` instead.
The `migration_steps` table records each one that has run, so it runs once per database.
Adding a column cannot add an index, a uniqueness rule or a required flag, so `app/schema_sync.py` then compares each table with its model and fixes what differs.
Postgres is changed in place. SQLite cannot drop a constraint, so the table is rebuilt with its rows and ids kept.
A column is only made required when it holds no empty values, and a column the models no longer have is kept and made optional.
It never drops a column.
Each change is logged as `migration: …` in the service log.
On start the service also gives every active company any connector types added since it was created.

Version 1.1 removed employee onboarding: its four tables are dropped on first start.
Clone Studio adds the clone, version, node, edge, group, access path, credential reference and sense point tables.
Existing signals gain empty clone, version, sense point and node columns, plus an origin of `live`. Old findings stay as they were.
Invented posture history (rows whose breakdown is empty) is deleted. Real daily snapshots are kept.
Companies created by 1.0 become *live* with their creation date as the go-live date, get the short name `org-<id>`, and start with an empty contract.
After upgrading, open each one in the operator console and fill in its contract, AI policy and contracted WhiteHats from *Settings*.

Seeded Azure models gain a `seed_slot` of `primary` or `optional`. A model a person added stays blank, and startup will not put the local key on it. The demo company's milestones and contract gain an origin of `sample`. A company a person creates stays `live`.
WhiteHat coverage that was stamped 0, 80 or 90 is cleared once, and the column becomes nullable with no default. Any other number is kept, and a number measured after the upgrade is never cleared.
Projects: every account gets a project named Main, existing rows move into it, and each project-scoped table gains a required, indexed project link. WhiteHats, posture days and clones become unique per project instead of per account.
Each cloud log and code connection is attached to Main with everything in scope, so it feeds exactly what it fed before. Identity, endpoint and ticketing connections stay account-wide.
CrowdStrike and Jira connectors become `unbuilt` and their saved secrets are cleared. An identity or GitHub connector that was `verified` becomes `checked`. A cloud log source becomes `reading` only after it has a last read time.
When you add a data step, add a test to `backend/tests/test_migrate.py` that builds the old shape and upgrades it.
Those tests run on SQLite and on Postgres, so a step that only one of them accepts fails the suite.

## Rolling back

Restore the database backup taken before the upgrade, deploy the previous code and restart.
Columns added by the newer version are harmless to the older one.
