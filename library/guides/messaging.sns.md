# Amazon SNS topic: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `sns:GetTopicAttributes`, `sns:ListSubscriptionsByTopic`.

## Customer-side steps

1. Nothing extra: the read-only role reads topic attributes and subscriptions.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call sns:GetTopicAttributes with the read-only role, expect 200) and starts the WhiteHat modes chosen for this sense point (messaging).

## Verification

Every subscription of `{{ properties.topic_name }}` is listed with its protocol.

## Rollback

Nothing to remove.
