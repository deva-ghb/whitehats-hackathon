# Google Cloud

WhiteHats reads one Google Cloud project the same way it reads an AWS account: through a read-only reader you create, with no key stored anywhere.

## What it reads

- The project's resources and their settings: buckets, firewall rules, networks, virtual machines, Cloud SQL databases, service accounts and their keys, Cloud Run, GKE, BigQuery datasets and Pub/Sub topics.
- Who has access to the project, its buckets, datasets, topics and Cloud Run services.
- The admin activity log: who changed what, and when.

It never reads files in buckets, rows in tables, messages or secret values, and it cannot change anything.
Your reader's roles allow no writes, and WhiteHats also refuses any call that is not on its short list of reads.

## What it flags

Every minute it checks the settings and reads the new activity.

| Check | Severity |
| --- | --- |
| A bucket, dataset, topic or the project open to anyone on the internet | Critical |
| A firewall rule letting the whole internet reach every port | Critical |
| A database accepting connections from anywhere | Critical |
| A firewall rule opening SSH, remote desktop or a database port to the internet | High |
| A database with no automated backups | High |
| A downloadable service account key | Medium |
| A bucket where single files can be shared on their own | Medium |
| A Cloud Run service anyone can call | Low |

A risky change in the activity log (access opened, a firewall rule added, a key created, a log export deleted) is flagged with who made it and when.
A problem closes on its own when the setting is put right.

## Connect

1. Run the setup script with your project ID and the identity WhiteHats runs as:

   ```
   docs/connectors/gcp-readonly-setup.sh <project-id> serviceAccount:<whitehats-identity>
   ```

   It creates `whitehats-reader@<project-id>.iam.gserviceaccount.com` with view roles only, and lets WhiteHats act as it.
2. In Connectors, configure Google Cloud with the project ID and the reader's email, then save.
   WhiteHats signs in as the reader and tries each read before it says the connection works.
