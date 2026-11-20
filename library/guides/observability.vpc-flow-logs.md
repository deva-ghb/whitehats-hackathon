# Amazon VPC Flow Logs: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Grant the read-only role read access to the flow log destination of `{{ properties.flow_log_id }}`.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call ec2:DescribeFlowLogs with the read-only role, expect status ACTIVE) and starts the WhiteHat modes chosen for this sense point (network).

## Verification

Flow records arrive within one sync.

## Rollback

Remove the read grant.
