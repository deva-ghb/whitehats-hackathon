# GitHub push listener: deploy guide

The address GitHub calls when someone pushes or opens a pull request. WhiteHats checks the signature on every delivery and refuses any that is not signed with the webhook secret.

## Prerequisites

- The WhiteHats GitHub App is installed on the repositories to watch.
- The platform has the webhook secret set, and the same secret is saved in the GitHub App's settings.

## Customer-side steps

1. In the GitHub App's settings, set the webhook address to the platform's `/api/github/webhook` address and tick Active.
2. Paste the same secret into the App's webhook secret field.

## Runner-side steps

Automatic once the steps above are done: a push is seen within seconds. If the webhook is switched off, WhiteHats finds the push by polling within a minute or so.

## Verification

Push to a branch of a watched repository. The change shows on the platform within seconds, and a delivery signed with another secret is refused.

## Rollback

Untick Active in the GitHub App's settings. WhiteHats then reads pushes by polling.
