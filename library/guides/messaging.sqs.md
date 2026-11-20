# Amazon SQS queue: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `sqs:GetQueueAttributes`, `sqs:ListQueues`.

## Customer-side steps

1. Nothing extra: the read-only role reads queue attributes. WhiteHats never receives messages from `{{ properties.queue_name }}`.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call sqs:GetQueueAttributes with the read-only role, expect 200; never receive messages) and starts the WhiteHat modes chosen for this sense point (messaging).

## Verification

The queue's policy, encryption and dead-letter queue appear in the inspector.

## Rollback

Nothing to remove.
