# Azure Container Registry: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `AcrPull`.

## Customer-side steps

1. Assign `AcrPull` on `{{ properties.registry_name }}` to the WhiteHats service principal.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list repositories with the AcrPull role, expect 200) and starts the WhiteHat modes chosen for this sense point (supply_chain).

## Verification

Repositories and image digests are listed.

## Rollback

Remove the role assignment.
