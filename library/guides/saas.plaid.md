# Plaid: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `client id and secret for read-only dashboard access`.

## Customer-side steps

1. Record which Plaid products and webhooks the services use; WhiteHats reads the configuration, not customer data.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call /institutions/get with a count of 1, expect 200) and starts the WhiteHat modes chosen for this sense point (saas).

## Verification

The account, its webhooks and its key scopes appear in the inspector.

## Rollback

Nothing to remove.
