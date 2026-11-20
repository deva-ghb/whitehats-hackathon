# Amazon EKS cluster: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `eks:DescribeCluster`, `eks:ListNodegroups`, `Kubernetes view ClusterRole`.

## Customer-side steps

1. Turn on the `audit` and `authenticator` control plane logs for `{{ properties.cluster_name }}`.
2. Map the WhiteHats read-only role to the built-in `view` ClusterRole through an EKS access entry.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (call eks:DescribeCluster, then list namespaces with the view role, expect 200) and starts the WhiteHat modes chosen for this sense point (iac, identity, telemetry).

## Verification

The Studio lists namespaces and workloads, and audit events arrive in CloudWatch Logs.

## Rollback

Delete the access entry. Turn the extra control plane logs off if they were off.
