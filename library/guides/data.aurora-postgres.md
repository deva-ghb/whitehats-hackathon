# Amazon Aurora PostgreSQL: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `rds:DescribeDBClusters`, `rds:DescribeDBClusterSnapshots`.

## Customer-side steps

1. Turn on database activity or audit logs for `{{ properties.cluster_identifier }}` (pgaudit to CloudWatch Logs) if they are off.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call rds:DescribeDBClusters with the read-only role, expect 200; never connect to the database) and starts the WhiteHat modes chosen for this sense point (data, iac).

## Verification

Connection and snapshot events appear; WhiteHats never logs in to the database.

## Rollback

Turn the audit logs off if they were off.
