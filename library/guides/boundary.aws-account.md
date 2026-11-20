# AWS account: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `ReadOnlyAccess minus s3:GetObject and dynamodb:GetItem`, `sts:AssumeRole with an external id`.

## Customer-side steps

1. Deploy the WhiteHats CloudFormation template in account `{{ properties.account_id }}`. It creates a read-only role with an external id and denies reading object and item contents.
2. Paste the role ARN into the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (assume the read-only role with the external id and call sts:GetCallerIdentity, expect this account id) and starts the WhiteHat modes chosen for this sense point (identity, iac).

## Verification

`sts:GetCallerIdentity` through the role returns account `{{ properties.account_id }}`, and a probe of `s3:GetObject` is refused.

## Rollback

Delete the CloudFormation stack. Nothing else was created.
