# Azure Front Door: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Send the `FrontDoorAccessLog` and `FrontDoorWebApplicationFirewallLog` categories of `{{ properties.profile_name }}` to the Log Analytics workspace.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the Front Door profile with the Reader role, expect 200) and starts the WhiteHat modes chosen for this sense point (network, iac).

## Verification

Access and WAF records arrive in Log Analytics within one sync.

## Rollback

Remove the diagnostic setting.
