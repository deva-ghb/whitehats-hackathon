# GitHub Actions workflows: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `actions:read`, `contents:read`.

## Customer-side steps

1. Grant the WhiteHats GitHub App `actions:read` on `{{ properties.repository }}`.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list workflow runs with the GitHub App, expect 200) and starts the WhiteHat modes chosen for this sense point (supply_chain, identity, secrets).

## Verification

Every workflow, its triggers, its secret names (never values) and its cloud role appear in the inspector.

## Rollback

Remove the `actions:read` permission.
