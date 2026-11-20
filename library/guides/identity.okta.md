# Okta: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `okta.logs.read`, `okta.users.read`, `okta.groups.read`, `okta.apps.read`.

## Customer-side steps

1. Create an OAuth service app in `{{ properties.org_url }}` with `okta.logs.read`, `okta.users.read`, `okta.groups.read` and `okta.apps.read`, and paste its credentials through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read /api/v1/org with the read-only token, expect 200) and starts the WhiteHat modes chosen for this sense point (identity).

## Verification

System Log events arrive and admin roles are listed.

## Rollback

Deactivate and delete the service app.
