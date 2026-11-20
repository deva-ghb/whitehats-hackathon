# AWS CloudTrail: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `cloudtrail:LookupEvents`, `cloudtrail:GetTrailStatus`.

## Customer-side steps

1. Make sure `{{ properties.trail_name }}` is logging. The read-only role reads events through `cloudtrail:LookupEvents` or from the trail bucket.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call cloudtrail:GetTrailStatus with the read-only role, expect IsLogging true) and starts the WhiteHat modes chosen for this sense point (identity, telemetry).

## Verification

Management events arrive within one sync; stopping the trail raises a critical signal.

## Rollback

Nothing to remove.
