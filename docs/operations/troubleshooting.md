# Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| A collector shows *crashed* | Its process exited | Open the collector; its output is in the log file shown there. Fix the cause and press *Start again* |
| A collector shows *pending* forever | It cannot reach `WH_PUBLIC_URL`, or the token is wrong | From the host, `curl $WH_PUBLIC_URL/api/health`. Rotate the token and redeploy |
| *Unknown collector token* in collector output | Token rotated or collector deleted | Rotate the token in the console and update the Secret or unit file |
| Cloud connector shows *error* | Wrong credentials, missing permission or wrong log group | The message names the cause. See the connector's guide for the exact permissions |
| Answers say *Rules engine* | No allowed model is reachable | *AI models → Test*. Check the key, the deployment name and the company's AI policy |
| *Support access to this company has ended* | The customer revoked access or it expired | Ask the customer to grant it again from *Setup* |
| Invitation says *expired* | Older than 72 hours | Revoke it and send a new one |
| Domain check fails | TXT record missing or DNS not propagated | `dig TXT <domain>` must show the exact `whitehats-verify=` value. Allow up to an hour |
| Port already in use on start | Another program uses 8000 or 8797 | Stop it, or set `WH_PORT` and `FACTORY_PORT` before `./start.sh` |
| `npm install` fails with EACCES in `~/.npm` | Cache files owned by root | `sudo chown -R $(whoami) ~/.npm` |
