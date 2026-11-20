# Terraform workspace: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `read state outputs only (Terraform Cloud: read-state-versions)`, `s3:GetObject on the state key only`.

## Customer-side steps

1. Give the read-only identity read access to the state of workspace `{{ properties.workspace }}` (Terraform Cloud: a team token with read state versions; S3: `s3:GetObject` on the state key only).

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the latest state version metadata, expect 200) and starts the WhiteHat modes chosen for this sense point (iac, secrets, supply_chain).

## Verification

The Studio imports the resources in the state and marks them `imported`.

## Rollback

Revoke the token or remove the read grant.
