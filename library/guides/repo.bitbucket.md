# Bitbucket repository: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `repository:read`.

## Customer-side steps

1. Create a repository access token on `{{ properties.url }}` with `repository:read` (add `pullrequest:write` only if Big Hat may open pull requests), and paste it through the secure entry link.
2. For Data Center behind a VPN, make sure the Runner's access path reaches the server.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list the default branch using the credential, expect 200) and starts the WhiteHat modes chosen for this sense point (code, supply_chain, secrets).

## Verification

The Studio reads the default branch and lists recent commits.

## Rollback

Revoke the access token.
