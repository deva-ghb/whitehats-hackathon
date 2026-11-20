# Azure Log Analytics workspace: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `Log Analytics Reader`.

## Customer-side steps

1. Assign `Log Analytics Reader` on workspace `{{ properties.workspace_id }}` to the WhiteHats service principal.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (run a one-row query with Log Analytics Reader, expect 200) and starts the WhiteHat modes chosen for this sense point (telemetry, identity).

## Verification

Rows from each listed table arrive within one sync.

## Rollback

Remove the role assignment.
