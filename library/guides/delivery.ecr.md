# Amazon ECR repository: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `ecr:GetAuthorizationToken`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer`.

## Customer-side steps

1. Add `ecr:GetAuthorizationToken`, `ecr:BatchGetImage` and `ecr:GetDownloadUrlForLayer` on `{{ properties.repository_name }}` to the read-only role, so images can be scanned without write access.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call ecr:DescribeImages with the read-only role, expect 200) and starts the WhiteHat modes chosen for this sense point (supply_chain).

## Verification

The latest image digest and its scan result appear in the inspector.

## Rollback

Remove the three actions from the role.
