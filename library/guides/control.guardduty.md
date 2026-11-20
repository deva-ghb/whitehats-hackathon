# Amazon GuardDuty: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing extra: the read-only role reads detector `{{ properties.detector_id }}` and its findings.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call guardduty:GetDetector with the read-only role, expect status ENABLED) and starts the WhiteHat modes chosen for this sense point (identity, network).

## Verification

Findings arrive and disabling the detector raises a signal.

## Rollback

Nothing to remove.
