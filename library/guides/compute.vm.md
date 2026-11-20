# Virtual machine: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Send the VM's system and authentication logs to the account's log service (CloudWatch agent or Azure Monitor agent).
2. Make sure the read-only role can read the instance and its network interfaces.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (describe the instance through the read-only role, expect it to be listed) and starts the WhiteHat modes chosen for this sense point (network, identity, telemetry, memory).

## Verification

Sign-in lines from `{{ node.name }}` arrive within one sync.

## Rollback

Remove the log agent configuration if it was added for WhiteHats.
