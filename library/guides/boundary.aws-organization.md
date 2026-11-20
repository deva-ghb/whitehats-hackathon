# AWS Organization: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `organizations:Describe*`, `organizations:List*`.

## Customer-side steps

1. In the management account, create the role `whitehats-org-reader` with `organizations:Describe*` and `organizations:List*` only, trusting the WhiteHats Runner role.
2. Share the role ARN through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call organizations:DescribeOrganization with the read-only role, expect the organization id) and starts the WhiteHat modes chosen for this sense point (identity, iac).

## Verification

The Studio lists every member account and shows each service control policy.

## Rollback

Delete the role from the management account.
