# QuickBooks Online: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `com.intuit.quickbooks.accounting read`.

## Customer-side steps

1. Record which app syncs with company `{{ properties.realm_id }}` and with which scopes.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read CompanyInfo with the OAuth token, expect 200) and starts the WhiteHat modes chosen for this sense point (saas).

## Verification

The account, its webhooks and its key scopes appear in the inspector.

## Rollback

Nothing to remove.
