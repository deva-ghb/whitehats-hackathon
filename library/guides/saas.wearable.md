# Wearable data platform: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A person who knows this part of the estate can answer the questions below.

## Customer-side steps

1. Record the platform, the OAuth scopes patients grant and the webhook endpoint that receives data.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (a person confirms the platform, the scopes granted and the webhook endpoint) and starts the WhiteHat modes chosen for this sense point (saas, data).

## Verification

The data flow and its scopes appear in the inspector.

## Rollback

Nothing to remove.
