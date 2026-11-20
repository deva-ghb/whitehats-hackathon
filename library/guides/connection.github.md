# GitHub connection: deploy guide

A connection is set up once for the account and attached to this project with a scope. It reads the repositories, their pipelines and the webhook that listens for pushes. WhiteHats only reads.

## Prerequisites

- Someone who may manage connections for the account.
- The setup guide for this connector in the help pages (`connectors/github`).

## Customer-side steps

1. Create the read-only access the setup guide describes. Nothing in it can change anything.
2. Enter what it gives you in the connection's panel on the canvas. Secrets are stored encrypted and never appear in the clone.

## Runner-side steps

Automatic once the steps above are done: the connection checks its own access, reads what it can see and shows the result on the node.

## Verification

The node says every permission is ok, when it last read, and how many things it found. A missing permission is named, with what it was needed for.

## Rollback

Disconnect the connection from its panel. Remove the read-only access you created. Nothing else was created.
