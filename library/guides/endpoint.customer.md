# Customers or patients: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A person who knows this part of the estate can answer the questions below.

## Customer-side steps

1. Record the channels customers use (web, mobile) and the identity provider they sign in with.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (a person confirms how customers reach the service and sign in) and starts the WhiteHat modes chosen for this sense point (identity).

## Verification

The persona links to its identity provider and the services it reaches.

## Rollback

Nothing to remove.
