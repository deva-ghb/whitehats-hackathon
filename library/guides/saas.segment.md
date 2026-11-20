# Segment: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `Workspace Read-only token`.

## Customer-side steps

1. Create a workspace read-only token for `{{ properties.workspace }}` and paste it through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list sources with the read-only token, expect 200) and starts the WhiteHat modes chosen for this sense point (saas, data).

## Verification

Sources and destinations are listed.

## Rollback

Revoke the token.
