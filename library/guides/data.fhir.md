# FHIR service (Azure Health Data Services): deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Send the `AuditLogs` category of `{{ properties.service_name }}` to the Log Analytics workspace.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the FHIR service with the Reader role, expect 200; never read resources) and starts the WhiteHat modes chosen for this sense point (data, identity).

## Verification

Audit records with the caller and the resource type arrive; WhiteHats never reads patient data.

## Rollback

Remove the diagnostic setting.
