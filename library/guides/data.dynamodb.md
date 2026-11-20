# Amazon DynamoDB table: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `dynamodb:DescribeTable`.

## Customer-side steps

1. Optionally turn on CloudTrail data events for `{{ properties.table_name }}` so bulk reads are visible. The read-only role is refused `dynamodb:GetItem` by design.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call dynamodb:DescribeTable with the read-only role, expect 200; never read items) and starts the WhiteHat modes chosen for this sense point (data, iac).

## Verification

The table's settings appear, and a probe of reading an item is refused.

## Rollback

Turn the data events off if they were off.
