# Microsoft Entra tenant: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `Directory.Read.All`, `AuditLog.Read.All`.

## Customer-side steps

1. Register the WhiteHats application in tenant `{{ properties.tenant_id }}` with `Directory.Read.All` and `AuditLog.Read.All` application permissions, and grant admin consent.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read /organization through Microsoft Graph, expect this tenant id) and starts the WhiteHat modes chosen for this sense point (identity).

## Verification

The Studio lists subscriptions and directory roles from this tenant.

## Rollback

Delete the app registration.
