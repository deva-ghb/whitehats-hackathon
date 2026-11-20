# Datadog: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `logs_read_data`, `apm_read`.

## Customer-side steps

1. Create a Datadog application key scoped to `logs_read_data` and `apm_read` on a service account, and paste it with an API key through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call /api/v1/validate with the application key, expect valid true) and starts the WhiteHat modes chosen for this sense point (telemetry).

## Verification

Log queries return lines within one sync.

## Rollback

Revoke the application key.
