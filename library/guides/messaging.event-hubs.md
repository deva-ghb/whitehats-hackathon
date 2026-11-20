# Azure Event Hubs: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `Reader`.

## Customer-side steps

1. Nothing extra: the subscription Reader role reads `{{ properties.namespace }}`.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the namespace with the Reader role, expect 200; never read events) and starts the WhiteHat modes chosen for this sense point (messaging).

## Verification

Every hub and its consumer groups appear.

## Rollback

Nothing to remove.
