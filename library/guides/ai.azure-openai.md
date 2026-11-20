# Azure OpenAI: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Send the `RequestResponse` and `Audit` categories of `{{ properties.resource_name }}` to the Log Analytics workspace.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (list deployments with the Reader role, expect 200) and starts the WhiteHat modes chosen for this sense point (ai).

## Verification

Request records arrive with the deployment and caller.

## Rollback

Remove the diagnostic setting.
