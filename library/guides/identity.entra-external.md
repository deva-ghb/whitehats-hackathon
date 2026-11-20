# Microsoft Entra External ID (customers): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `AuditLog.Read.All`.

## Customer-side steps

1. Register the WhiteHats app in the external tenant `{{ properties.tenant_id }}` with `AuditLog.Read.All` and grant admin consent.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read sign-in logs through Microsoft Graph in the external tenant, expect 200) and starts the WhiteHat modes chosen for this sense point (identity).

## Verification

Customer sign-in events arrive.

## Rollback

Delete the app registration.
