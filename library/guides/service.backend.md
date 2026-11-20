# Backend service or API: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Make sure the service's repository is connected (Code mode) and its log group or workspace is a telemetry node.
2. Write logs as JSON with a request id, the route, the status and the caller, and never personal data.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call the health URL from the access path, expect 200) and starts the WhiteHat modes chosen for this sense point (code, supply_chain, secrets, telemetry, pentest, memory).

## Verification

The service shows its repository, its runtime identity and its log source, all green.

## Rollback

Nothing was added to the service itself.
