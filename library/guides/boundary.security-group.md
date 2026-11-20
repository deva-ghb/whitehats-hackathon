# Security group or network security group: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing extra: the account or subscription read-only role reads the rules of `{{ node.name }}`.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the rules through the read-only role (ec2:DescribeSecurityGroupRules or Microsoft.Network/networkSecurityGroups/read), expect 200) and starts the WhiteHat modes chosen for this sense point (network, iac).

## Verification

Every inbound and outbound rule appears in the inspector, and rules open to the internet are flagged.

## Rollback

Nothing to remove.
