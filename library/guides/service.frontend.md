# Frontend web app: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Connect the app's repository so its packages and build settings can be read.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (load the site URL, expect 200 over HTTPS) and starts the WhiteHat modes chosen for this sense point (code, supply_chain, pentest).

## Verification

The app's dependencies and its Content Security Policy appear in the inspector.

## Rollback

Nothing was added to the app.
