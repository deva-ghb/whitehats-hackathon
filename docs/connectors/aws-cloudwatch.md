# AWS CloudWatch Logs

Reads CloudTrail and application logs from CloudWatch Logs, read-only, every 5 minutes.
CloudTrail events feed the **Cloud Posture** WhiteHat, which flags logging switched off, root account use, firewalls opened to the internet, new access keys and more.

## 1. Send CloudTrail to CloudWatch (if not already)

In the AWS console: *CloudTrail → Trails → your trail → CloudWatch Logs → Edit*, enable it and choose or create a log group, for example `cloudtrail`.
Any application log groups you want analysed can be added too.

## 2. Give WhiteHats read-only access

WhiteHats assumes a role, and always sends an external ID.
The connection is not saved without one, because anyone who learns the role ARN could otherwise ask WhiteHats to read that account.

Permissions policy (replace the region, account and log group names):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["logs:FilterLogEvents"],
    "Resource": [
      "arn:aws:logs:ap-south-1:111122223333:log-group:cloudtrail",
      "arn:aws:logs:ap-south-1:111122223333:log-group:cloudtrail:*"
    ]
  }]
}
```

Trust policy (the principal is the AWS account WhiteHats runs in; the external ID is any long random string you also enter in WhiteHats):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::<whitehats-account-id>:root"},
    "Action": "sts:AssumeRole",
    "Condition": {"StringEquals": {"sts:ExternalId": "<external-id>"}}
  }]
}
```

The WhiteHats server needs its own AWS credentials (an instance role, not a stored access key) allowed to call `sts:AssumeRole` on that role.

WhiteHats does not accept a long-lived access key for this connector.

## 3. Connect

*Connectors → AWS CloudWatch Logs → Connect*:

| Field | Example |
| --- | --- |
| AWS region | `ap-south-1` |
| Log groups | `cloudtrail, /aws/lambda/payments` |
| Read-only role ARN | `arn:aws:iam::111122223333:role/whitehats-reader` |
| External ID | the value from the trust policy |

*Save and check* signs in as the role and reads up to 5 recent events.
WhiteHats needs only two permissions: `sts:AssumeRole` on the role, and `logs:FilterLogEvents` on the log groups you name.
After that, the background worker syncs every 5 minutes and *Read now* reads immediately.
A cursor per log group means each event is read once.

## What you see

- The connector appears as a cloud log source on *Collectors*, with its own context budget.
- Shapes appear under *Collectors*, on the *Logs* tab.
- Risky CloudTrail events become signals from the Cloud Posture WhiteHat; critical and high ones also go to the alerts channel.
- If the check fails, the card says *Check failed* with the reason; fix it and save again.
- If a read fails after reading has worked, the card says *Stopped reading* with the reason and the last good read. The 5-minute sync keeps trying and the card returns to *Reading data* by itself; an admin can also press *Try again*.
