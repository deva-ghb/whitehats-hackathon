# Azure subscription: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `Reader`, `Log Analytics Reader`.

## Customer-side steps

1. Assign `Reader` on subscription `{{ properties.subscription_id }}` to the WhiteHats service principal.
2. Export the Activity Log to the Log Analytics workspace if it is not already.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the subscription with the Reader role, expect state Enabled) and starts the WhiteHat modes chosen for this sense point (identity, iac).

## Verification

Resources in the subscription appear on the canvas and Activity Log events arrive.

## Rollback

Remove the role assignment and, if added, the diagnostic setting.
