# Amazon CloudWatch Logs: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `logs:FilterLogEvents`, `logs:DescribeLogGroups`.

## Customer-side steps

1. Grant the read-only role `logs:FilterLogEvents` and `logs:DescribeLogGroups` on the log groups `{{ properties.log_groups }}`.
2. Set an explicit retention: WhiteHats can only look back as far as the logs are kept.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call logs:FilterLogEvents on one group with limit 1, expect 200) and starts the WhiteHat modes chosen for this sense point (telemetry).

## Verification

Log lines arrive within one sync and the cursor advances.

## Rollback

Remove the two actions from the role.
