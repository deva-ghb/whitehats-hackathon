# Amazon API Gateway: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Turn on access logging for every stage of `{{ properties.api_id }}` to a CloudWatch log group, with the request id, route, status and caller fields.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call apigateway:GET on the API with the read-only role, expect the api id) and starts the WhiteHat modes chosen for this sense point (network, iac, pentest).

## Verification

Access log lines arrive for each route within one sync.

## Rollback

Turn access logging off if it was off before.
