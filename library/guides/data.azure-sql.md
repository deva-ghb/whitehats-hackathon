# Azure SQL Database: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Turn on auditing for `{{ properties.server_name }}` to the Log Analytics workspace.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the server and database with the Reader role, expect 200; never connect to the database) and starts the WhiteHat modes chosen for this sense point (data, iac).

## Verification

Login and permission-change audit records arrive.

## Rollback

Turn auditing off if it was off.
