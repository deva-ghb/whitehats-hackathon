# Amazon MSK (Kafka): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `kafka:DescribeClusterV2`, `kafka-cluster:DescribeTopic`.

## Customer-side steps

1. Nothing extra for configuration. To list topics, grant `kafka-cluster:Connect` and `kafka-cluster:DescribeTopic` on `{{ properties.cluster_name }}`, never read access to records.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call kafka:DescribeClusterV2 with the read-only role, expect 200; never consume records) and starts the WhiteHat modes chosen for this sense point (messaging, network).

## Verification

The cluster's listeners, auth and topics appear.

## Rollback

Remove the Kafka cluster permissions.
