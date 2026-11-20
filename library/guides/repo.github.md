# GitHub repository: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `contents:read`, `metadata:read`, `pull_requests:write only for comments`, `checks:write`.

## Customer-side steps

1. Install the WhiteHats GitHub App on `{{ properties.url }}` with contents and metadata read, checks write, and pull requests write for comments only.
2. Leave webhooks on so pushes reach the Runner within seconds.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the default branch with the GitHub App installation token, expect 200) and starts the WhiteHat modes chosen for this sense point (code, supply_chain, secrets).

## Verification

The Studio reads the default branch and a test push appears within 10 seconds.

## Rollback

Uninstall the GitHub App from this repository.
