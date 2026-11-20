# SendGrid: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `API key with read-only access`.

## Customer-side steps

1. Create a read-only API key and paste it through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read /v3/scopes with the API key, expect 200) and starts the WhiteHat modes chosen for this sense point (saas).

## Verification

The account, its webhooks and its key scopes appear in the inspector.

## Rollback

Delete the API key.
