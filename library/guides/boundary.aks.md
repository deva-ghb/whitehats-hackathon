# Azure Kubernetes Service cluster: deploy guide

This template is filled with the node's own properties when a sense point is accepted.

## Prerequisites

- The access path for `{{ node.name }}` is up (`{{ node.access_path }}`).
- A read-only credential is bound to the node, with only: `Azure Kubernetes Service Cluster User Role`, `Kubernetes view ClusterRole`.

## Customer-side steps

1. Send the `kube-audit-admin` diagnostic category of `{{ properties.cluster_name }}` to the Log Analytics workspace.
2. Bind the WhiteHats service principal to the `view` ClusterRole.

## Runner-side steps

Automatic once the steps above are done: the Runner runs the connection test (read the managed cluster, then list namespaces with the view role, expect 200) and starts the WhiteHat modes chosen for this sense point (iac, identity, telemetry).

## Verification

The Studio lists namespaces and workloads, and audit events arrive in Log Analytics.

## Rollback

Remove the role binding and the diagnostic setting.
