# AWS IAM role: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing extra: the read-only role reads `{{ properties.role_name }}`, its trust policy and its attached policies.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call iam:GetRole with the read-only role, expect 200) and starts the WhiteHat modes chosen for this sense point (identity).

## Verification

The role's trust policy and effective permissions appear in the inspector.

## Rollback

Nothing to remove.
