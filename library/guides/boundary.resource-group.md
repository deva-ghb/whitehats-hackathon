# Azure resource group: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing extra: the subscription Reader role covers resource groups.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the resource group with the subscription Reader role, expect 200) and starts the WhiteHat modes chosen for this sense point (iac).

## Verification

Every resource in `{{ properties.name }}` appears inside the group on the canvas.

## Rollback

Nothing to remove.
