# Serverless function: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Make sure the function's log group (or Application Insights) is a telemetry node and the read-only role can read it.
2. Move any secret in an environment variable into the secrets store.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the function configuration with the read-only role, expect the runtime) and starts the WhiteHat modes chosen for this sense point (code, supply_chain, secrets, telemetry, identity).

## Verification

The function shows its trigger, its execution role and its log source.

## Rollback

Nothing was added to the function.
