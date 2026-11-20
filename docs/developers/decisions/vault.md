# Vault

**Status:** accepted
**Date:** 2026-10-19

Secrets live in our managed vault, one key per tenant. Customer admins enter credentials through a secure link. Our engineers do not type those secrets into the Studio. This is D1a and D3a in the build spec, section 1.

The other places considered were the customer's own vault (AWS Secrets Manager or Azure Key Vault), referenced by the Runner, or letting each customer choose. A customer vault would leave the product unable to open a credential when that vault is down or the Runner is not yet installed. One key per tenant means a compromise of one company's key does not open the others.

The managed store may be HashiCorp Vault or a cloud KMS-backed store. That is one decision, not a second one: the store is ours, and the clone document never holds a secret value. Write access stays refused unless a named approver allows it, and then only so Big Hat can open a pull request (D2a).
