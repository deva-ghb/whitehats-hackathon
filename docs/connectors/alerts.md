# Alerts

Critical and high signals are sent to the customer's channel as they happen.
Set it under *Setup → Alerts* and press *Send test*; the readiness check passes when the test is delivered.

## Slack

1. Create a Slack app (*api.slack.com/apps → Create*), enable *Incoming Webhooks* and add one to your security channel.
2. Paste the `https://hooks.slack.com/services/…` address into WhiteHats.

## Microsoft Teams

1. In the channel, add a Workflow from the template *Post to a channel when a webhook request is received*.
2. In the workflow, post the incoming request's `text` field as the message.
3. Paste the workflow's `https://…` address into WhiteHats.

WhiteHats sends `{"text": "…"}`.

## Email

Requires `WH_SMTP_HOST` (and usually `WH_SMTP_USER`, `WH_SMTP_PASSWORD`) on the server.
Enter a distribution list address, not a person.
