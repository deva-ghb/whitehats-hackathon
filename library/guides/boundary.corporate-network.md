# Corporate network (trust zone): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A person who knows this part of the estate can answer the questions below.

## Customer-side steps

1. List the address ranges of `{{ node.name }}` and the VPN product used to join it.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (a person confirms the address ranges and who can join this network) and starts the WhiteHat modes chosen for this sense point (network, identity).

## Verification

Edges from this zone into the cloud show the VPN as their route.

## Rollback

Nothing to remove.
