# Staff and their managed devices: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A person who knows this part of the estate can answer the questions below.

## Customer-side steps

1. Record which identity provider staff sign in with and how their devices are managed.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (a person confirms the group, its sign-in method and its device management) and starts the WhiteHat modes chosen for this sense point (identity).

## Verification

The group links to its identity provider.

## Rollback

Nothing to remove.
