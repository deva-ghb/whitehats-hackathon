# Azure API Management: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Send the `GatewayLogs` category of `{{ properties.service_name }}` to the Log Analytics workspace.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the API Management service with the Reader role, expect 200) and starts the WhiteHat modes chosen for this sense point (network, iac, pentest).

## Verification

Gateway records arrive within one sync.

## Rollback

Remove the diagnostic setting.
