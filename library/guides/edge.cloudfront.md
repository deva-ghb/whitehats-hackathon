# Amazon CloudFront distribution: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Turn on standard logging for distribution `{{ properties.distribution_id }}` to a log bucket the read-only role can read.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call cloudfront:GetDistributionConfig with the read-only role, expect the distribution id) and starts the WhiteHat modes chosen for this sense point (network, iac).

## Verification

Request logs from the distribution arrive within one sync.

## Rollback

Turn the logging off if it was off before.
