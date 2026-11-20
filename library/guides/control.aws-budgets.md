# AWS Budgets alarm: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Allow the read-only role `budgets:ViewBudget` so `{{ properties.budget_name }}` can be read.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call budgets:DescribeBudget with a role that may read budgets, expect 200) and starts the WhiteHat modes chosen for this sense point (iac).

## Verification

The limit, thresholds and current spend appear in the inspector.

## Rollback

Remove `budgets:ViewBudget` from the role.
