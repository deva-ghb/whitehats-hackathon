#!/usr/bin/env bash
# Creates the read-only reader WhiteHats uses for one Google Cloud project. The Google Cloud twin of the AWS
# CloudFormation template (aws-readonly-role.yaml).
#
#   ./gcp-readonly-setup.sh <project-id> <whitehats-principal>
#
# <whitehats-principal> is the identity WhiteHats runs as, e.g. "serviceAccount:runner@whitehats.iam.gserviceaccount.com"
# or, for a local trial, "user:you@example.com". It may only act as the reader; it gets no role on the project itself.
#
# The reader can see configuration, permissions and the admin activity log. It cannot change anything and cannot
# read data inside buckets, databases or messages: none of the roles below grant that.
set -euo pipefail
project="${1:?project id}"
principal="${2:?whitehats principal}"
reader="whitehats-reader"
email="$reader@$project.iam.gserviceaccount.com"

gcloud services enable cloudasset.googleapis.com iamcredentials.googleapis.com iam.googleapis.com \
  cloudresourcemanager.googleapis.com --project "$project"

gcloud iam service-accounts describe "$email" --project "$project" >/dev/null 2>&1 ||
  gcloud iam service-accounts create "$reader" --project "$project" \
    --display-name "WhiteHats read-only reader" \
    --description "Read-only: configuration, permissions and admin activity. No data, no changes."

# Configuration and permissions of every resource (Cloud Asset Inventory), the project's own settings, and the
# admin activity log. iam.securityReviewer lists permissions only; logging.viewer reads admin activity, not data access.
for role in roles/cloudasset.viewer roles/iam.securityReviewer roles/logging.viewer roles/browser; do
  gcloud projects add-iam-policy-binding "$project" --member "serviceAccount:$email" --role "$role" \
    --condition None --quiet >/dev/null
done

# WhiteHats signs in as the reader with short-lived tokens. No key is created or stored.
gcloud iam service-accounts add-iam-policy-binding "$email" --project "$project" \
  --member "$principal" --role roles/iam.serviceAccountTokenCreator --quiet >/dev/null

echo "Reader ready: $email"
