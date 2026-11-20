# AWS Secrets Manager: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `secretsmanager:ListSecrets`, `secretsmanager:DescribeSecret`.

## Customer-side steps

1. Nothing extra: the read-only role lists secrets and their rotation. It is refused `secretsmanager:GetSecretValue` by design.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call secretsmanager:ListSecrets with the read-only role, expect 200; never read a secret value) and starts the WhiteHat modes chosen for this sense point (identity, secrets).

## Verification

Secret names, rotation and last access appear; a probe of reading a value is refused.

## Rollback

Nothing to remove.
