# Microsoft Entra ID (staff): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `AuditLog.Read.All`, `Directory.Read.All`, `Application.Read.All`.

## Customer-side steps

1. Register the WhiteHats app with `AuditLog.Read.All`, `Directory.Read.All` and `Application.Read.All` application permissions and grant admin consent.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read /auditLogs/signIns?$top=1 through Microsoft Graph, expect 200) and starts the WhiteHat modes chosen for this sense point (identity).

## Verification

Sign-in and directory audit events arrive.

## Rollback

Delete the app registration.
