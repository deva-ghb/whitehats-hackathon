# Azure Application Insights: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Assign `Monitoring Reader` on `{{ properties.component_name }}` to the WhiteHats service principal.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (run a one-row query with the Reader role, expect 200) and starts the WhiteHat modes chosen for this sense point (telemetry).

## Verification

Requests and exceptions arrive within one sync.

## Rollback

Remove the role assignment.
