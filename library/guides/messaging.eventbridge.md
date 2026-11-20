# Amazon EventBridge bus: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing extra: the read-only role reads buses, rules and targets.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call events:DescribeEventBus with the read-only role, expect 200) and starts the WhiteHat modes chosen for this sense point (messaging, iac).

## Verification

Every rule and target on `{{ properties.bus_name }}` is listed.

## Rollback

Nothing to remove.
