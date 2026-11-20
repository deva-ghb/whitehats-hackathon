# Azure network flow logs: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Assign `Storage Blob Data Reader` on the flow log container in `{{ properties.storage_account }}` to the WhiteHats service principal.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list flow log blobs with Storage Blob Data Reader, expect 200) and starts the WhiteHat modes chosen for this sense point (network).

## Verification

Flow records arrive within one sync.

## Rollback

Remove the role assignment.
