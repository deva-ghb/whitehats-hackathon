# Generic security control: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `the least access this component allows for reading its configuration`.

## Customer-side steps

1. Describe `{{ node.name }}` in the inspector: what it is, who makes it and how it is reached.
2. Request a proper type from the library so this node can be upgraded.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (a person confirms what this is until a proper type replaces it) and starts the WhiteHat modes chosen for this sense point (iac).

## Verification

The node shows its description and an open library request.

## Rollback

Nothing to remove.
