# Amazon Route 53 hosted zone: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `route53:GetHostedZone`, `route53:ListResourceRecordSets`.

## Customer-side steps

1. Nothing extra: the account read-only role reads hosted zones. Turn on query logging if you want DNS queries watched.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call route53:GetHostedZone with the read-only role, expect the zone name) and starts the WhiteHat modes chosen for this sense point (iac, network).

## Verification

Every record in `{{ properties.zone_name }}` is listed, and dangling records are flagged.

## Rollback

Turn query logging off if it was off.
