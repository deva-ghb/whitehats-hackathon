# AWS Systems Manager Parameter Store: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `ssm:DescribeParameters`.

## Customer-side steps

1. Nothing extra: the read-only role describes parameters under `{{ properties.parameter_path }}`. It is refused `ssm:GetParameter` with decryption by design.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call ssm:DescribeParameters with the read-only role, expect 200; never read a value) and starts the WhiteHat modes chosen for this sense point (identity, secrets).

## Verification

Parameter names and types appear; a probe of reading a value is refused.

## Rollback

Nothing to remove.
