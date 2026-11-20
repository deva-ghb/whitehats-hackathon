# Azure Key Vault: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Send the `AuditEvent` category of `{{ properties.vault_name }}` to the Log Analytics workspace.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the vault with the Reader role, expect 200; never read a secret value) and starts the WhiteHat modes chosen for this sense point (identity, secrets).

## Verification

Secret access events arrive with the caller; WhiteHats never reads a value.

## Rollback

Remove the diagnostic setting.
