# Arize Phoenix (LLM tracing): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `API key with read access`.

## Customer-side steps

1. Create an API key with read access to project `{{ properties.project }}` and paste it through the secure entry link.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read one span with the API key, expect 200) and starts the WhiteHat modes chosen for this sense point (ai, telemetry).

## Verification

Spans arrive within one sync.

## Rollback

Delete the API key.
