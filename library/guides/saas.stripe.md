# Stripe: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `restricted key with read-only scopes`.

## Customer-side steps

1. Create a restricted key for account `{{ properties.account_id }}` with read-only access to the resources WhiteHats lists, and paste it through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call /v1/account with the restricted key, expect 200) and starts the WhiteHat modes chosen for this sense point (saas).

## Verification

The account, its webhooks and its key scopes appear in the inspector.

## Rollback

Delete the restricted key.
