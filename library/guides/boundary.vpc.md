# Virtual network (VPC or VNet): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Turn on flow logs for this network if they are off (AWS: VPC Flow Logs to CloudWatch Logs or S3; Azure: VNet flow logs to a storage account).
2. Grant the read-only role read access to the flow log destination.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (describe the network through the read-only role (ec2:DescribeVpcs or Microsoft.Network/virtualNetworks/read), expect the CIDR to match) and starts the WhiteHat modes chosen for this sense point (network, iac).

## Verification

The Studio shows flow log records from `{{ node.name }}` within one sync.

## Rollback

Turn flow logs off again if they were off before, and remove the read grant.
