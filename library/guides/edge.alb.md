# Application Load Balancer: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Turn on access logs for `{{ properties.load_balancer_name }}` to a log bucket the read-only role can read.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call elasticloadbalancing:DescribeLoadBalancers with the read-only role, expect the name) and starts the WhiteHat modes chosen for this sense point (network, iac).

## Verification

Access log files arrive within one sync.

## Rollback

Turn access logs off if they were off before.
