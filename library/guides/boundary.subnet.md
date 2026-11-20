# Subnet: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing extra: the subnet is read with its network. Flow logs on the network cover it.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (describe the subnet through the read-only role, expect the CIDR to match) and starts the WhiteHat modes chosen for this sense point (network, iac).

## Verification

The subnet's route table and exposure appear in the inspector.

## Rollback

Nothing to remove.
