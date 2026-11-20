# Azure Cache for Redis: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Nothing extra: the subscription Reader role reads `{{ properties.cache_name }}`.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the cache with the Reader role, expect 200) and starts the WhiteHat modes chosen for this sense point (data, network).

## Verification

Port, key and network settings appear in the inspector.

## Rollback

Nothing to remove.
