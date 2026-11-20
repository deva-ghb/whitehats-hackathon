# AWS region: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing to do in the region itself: the account role covers it. Make sure the region is in the role's allowed list if you restrict regions.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call ec2:DescribeRegions with the read-only role, expect the region to be listed as enabled) and starts the WhiteHat modes chosen for this sense point (iac).

## Verification

Resources in `{{ properties.region }}` appear on the canvas with provenance `discovered`.

## Rollback

Nothing to remove.
