# Credential store

Secrets live in our vault, one key per company. A clone document holds a reference such as `vault://tenant/acme/github-app` and never the value. This is the store chosen in [the vault decision](decisions/vault.md).

The company key is the one `security.tenant_box` already keeps: created the first time the company seals something, then wrapped with the platform master key `WH_SECRET_KEY`. Sealing a value for one company and opening it with another company's key fails. Losing the master key is a different event: it unwraps every company key. That is described in the [threat model](threat-model.md).

## What is stored

`vault_secrets` has one row per reference inside a company:

- `reference`, the vault path, which must name this company's slug
- `value_enc`, the value sealed with this company's key
- `scope`, words such as `repository:read`, which are not the secret

The reference uses the same rules as the clone document. It must start with `vault://tenant/<slug>/`, and it must not contain `..`, an empty segment, or a slug that is not this company. Those rules live in `library.clone_document.vault_problem`. The store checks the slug against the signed-in company again, so a reference that is well formed for someone else is still refused.

## Who can do what

| Call | Permission | What comes back |
| --- | --- | --- |
| `GET /api/vault/secrets` | `use_account` (anyone signed in to the account) | `reference`, `set`, `scope`, `updated_at` |
| `PUT /api/vault/secrets` | `manage_connections` (Account owner, Account admin) | the same three fields, without `updated_at` |

The body of a store is `reference`, `value` and an optional `scope`. The value is not written to the audit trail, the log, or the response. A scope or a reference that contains the value is refused. Storing the same reference again replaces the sealed value.

`open_secret` in `app/vault_store.py` is how a later connector reads the value. It is not an HTTP route. It refuses a reference for another company, and it opens the ciphertext with this company's key only.

## What an export contains

An offboarding export lists each vault reference and its scope. The sealed column is named `value_enc`, and the export skips every column whose name ends in `_enc`, so the value is not in the file. Deleting a company deletes its vault rows before the company row.

## The scan

`app/secret_scan.py` reads every cell of a database and the log, export and database files under a directory. `tests/test_vault.py` runs it over the suite database, the API responses from storing and exporting, the vault log and the offboarding export. A hit names the place, not the secret.
