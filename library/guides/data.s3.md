# Amazon S3 bucket: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `s3:GetBucketPolicy`, `s3:GetBucketPolicyStatus`, `s3:GetBucketPublicAccessBlock`, `s3:GetEncryptionConfiguration`.

## Customer-side steps

1. Optionally turn on CloudTrail data events for `{{ properties.bucket_name }}` so object reads are visible. The read-only role is refused `s3:GetObject` by design.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call s3:GetBucketPolicyStatus with the read-only role, expect 200; never read objects) and starts the WhiteHat modes chosen for this sense point (data, iac).

## Verification

The bucket's public access, encryption and policy appear, and a probe of reading an object is refused.

## Rollback

Turn the data events off if they were off.
