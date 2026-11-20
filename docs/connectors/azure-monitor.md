# Azure Monitor

Reads Azure Activity and Entra ID sign-in logs from a Log Analytics workspace, read-only, every 5 minutes.

## 1. Send logs to a workspace

- Activity Log: *Monitor → Activity log → Export Activity Logs → Add diagnostic setting*, send to your Log Analytics workspace.
- Sign-in logs (needs Entra ID P1 or P2): *Entra ID → Diagnostic settings → Add*, tick `SignInLogs`, send to the same workspace.

## 2. Create an app registration

1. *Entra ID → App registrations → New registration*, name it `whitehats-reader`.
2. *Certificates & secrets → New client secret*; copy the value.
3. On the workspace: *Access control (IAM) → Add role assignment → Log Analytics Reader*, assign it to `whitehats-reader`.

## 3. Connect

*Connectors → Azure Monitor → Connect*:

| Field | Where to find it |
| --- | --- |
| Directory (tenant) ID | App registration overview |
| Application (client) ID | App registration overview |
| Log Analytics workspace ID | Workspace overview |
| Tables to read | Optional KQL, default `union AzureActivity, SigninLogs` |
| Client secret | The value from step 2 |

WhiteHats signs in with the client-credentials flow and runs a read-only query for rows newer than its cursor.
