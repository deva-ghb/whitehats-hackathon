# Google Cloud Logging

Reads Cloud Audit Logs (and any logs you choose) from one project, read-only, every 5 minutes.

## 1. Create a service account

1. *IAM & Admin → Service accounts → Create*, name it `whitehats-reader`.
2. Grant **Logs Viewer** (`roles/logging.viewer`). Add **Private Logs Viewer** (`roles/logging.privateLogViewer`) only if you want Data Access audit logs.
3. *Keys → Add key → JSON*; download it.

Admin Activity audit logs are on by default; nothing else is needed for them.

## 2. Connect

*Connectors → Google Cloud Logging → Connect*:

| Field | Example |
| --- | --- |
| Project ID | `acme-prod` (optional; defaults to the key's project) |
| Log filter | Optional, default `logName:"cloudaudit.googleapis.com"` |
| Service account key | Paste the whole JSON file |

WhiteHats signs a short-lived token request with the key, then calls `entries:list` for entries newer than its cursor.
Delete the downloaded key file after pasting it.
