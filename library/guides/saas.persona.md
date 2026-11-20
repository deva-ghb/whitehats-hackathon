# Persona (identity verification): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `API key with read access to inquiries metadata`.

## Customer-side steps

1. Record the Persona environment, its webhooks and whether signatures are checked.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list one inquiry template with the API key, expect 200) and starts the WhiteHat modes chosen for this sense point (saas, data).

## Verification

The account, its webhooks and its key scopes appear in the inspector.

## Rollback

Nothing to remove.
