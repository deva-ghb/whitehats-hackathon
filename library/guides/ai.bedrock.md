# Amazon Bedrock: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- The read-only identity for the parent account or subscription is connected.

## Customer-side steps

1. Turn on model invocation logging to CloudWatch Logs, or connect the tracing tool that already records prompts.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call bedrock:GetModelInvocationLoggingConfiguration with the read-only role, expect 200) and starts the WhiteHat modes chosen for this sense point (ai).

## Verification

Invocations arrive with the model, caller and token counts.

## Rollback

Turn invocation logging off if it was off.
