# Amazon RDS database instance: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `rds:DescribeDBInstances`, `rds:DescribeDBSnapshots`.

## Customer-side steps

1. Turn on the engine's audit or connection logs for `{{ properties.db_instance_identifier }}` and publish them to CloudWatch Logs.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call rds:DescribeDBInstances with the read-only role, expect 200; never connect to the database) and starts the WhiteHat modes chosen for this sense point (data, iac).

## Verification

Connection and snapshot events appear; WhiteHats never logs in to the database.

## Rollback

Turn the extra logs off if they were off.
