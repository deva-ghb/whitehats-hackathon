# Azure Activity Log: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Export the Activity Log of subscription `{{ properties.subscription_id }}` to the Log Analytics workspace so it is kept beyond 90 days.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read one Activity Log event with the Reader role, expect 200) and starts the WhiteHat modes chosen for this sense point (identity, telemetry).

## Verification

Administrative events arrive within one sync.

## Rollback

Remove the export setting.
