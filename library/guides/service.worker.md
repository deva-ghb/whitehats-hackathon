# Worker or back-office service: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Connect the worker's repository and its log source.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the task or deployment with the read-only role, expect it to be running) and starts the WhiteHat modes chosen for this sense point (code, supply_chain, secrets, telemetry).

## Verification

The worker shows its queue or schedule, its identity and its log source.

## Rollback

Nothing was added to the worker.
