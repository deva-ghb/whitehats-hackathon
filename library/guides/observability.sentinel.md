# Microsoft Sentinel: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `Microsoft Sentinel Reader`.

## Customer-side steps

1. Assign `Microsoft Sentinel Reader` on the workspace `{{ properties.workspace_id }}` to the WhiteHats service principal.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list incidents with Microsoft Sentinel Reader, expect 200) and starts the WhiteHat modes chosen for this sense point (telemetry).

## Verification

Analytics rules and open incidents are listed.

## Rollback

Remove the role assignment.
