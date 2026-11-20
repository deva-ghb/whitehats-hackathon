# Amazon Redshift: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Turn on audit logging for `{{ properties.cluster_identifier }}` to CloudWatch Logs.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call redshift:DescribeClusters with the read-only role, expect 200; never run queries) and starts the WhiteHat modes chosen for this sense point (data).

## Verification

Connection and user activity logs arrive.

## Rollback

Turn audit logging off if it was off.
