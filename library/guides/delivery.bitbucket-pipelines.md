# Bitbucket Pipelines: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `pipeline:read`, `repository:read`.

## Customer-side steps

1. Create a workspace access token for `{{ properties.workspace }}` with `pipeline:read` and `repository:read`, and paste it through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list recent pipelines with the access token, expect 200) and starts the WhiteHat modes chosen for this sense point (supply_chain, identity, secrets).

## Verification

Each pipeline, its deployment environments and its variable names (never values) appear.

## Rollback

Revoke the access token.
