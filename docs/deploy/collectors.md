# Collectors

Collectors are small, read-only agents that send data to WhiteHats.
One program (`backend/agent/collector.py`, Python 3 plus `psutil`) does three jobs, chosen with `--kind`:

| Kind | Reads | Sends |
| --- | --- | --- |
| `log` | Log files (tails them, survives rotation) | New lines, in batches of up to 2,000 |
| `network` | Live connections and listening ports | A snapshot every interval |
| `process` | The busiest processes | A snapshot every interval |

A collector only reads, only talks to WhiteHats, and authenticates with its own token.
WhiteHats stores only a fingerprint of the token, so copy it when it is shown.

## Deploying

In the customer's console: *Collectors → Deploy a collector*.
Choose what to collect and where it runs, set the report interval and the AI context budget, and press create.
The console shows the token and ready-to-use files for every option below.

### Run here now

Starts the collector as a process on the WhiteHats server itself.
Use it for a first look and for demos.

### Kubernetes (DaemonSet)

The generated file creates a `whitehats` namespace, a service account with no API token, a Secret holding the collector token, an egress-only network policy (443 and DNS) and a DaemonSet that puts one pod on every node.

```sh
kubectl apply -f whitehats-collector.yaml
kubectl -n whitehats get pods
```

Pods run with a read-only root filesystem and no Linux capabilities.
Log collectors mount `/var/log` read-only; network analyzers and process monitors use the host network and process namespace so they see the node, not just the pod.

### Docker

One container per host:

```sh
docker run -d --name whitehats-log-collector -e WH_TOKEN=<token> --restart unless-stopped --read-only --cap-drop ALL \
  --security-opt no-new-privileges --memory 256m --cpus 0.25 -v /var/log:/host/var/log:ro \
  public.ecr.aws/f8g3u1i7/whitehats-collector:1.0.0 --server https://whitehats.example.com --kind log \
  --interval 15 --name web-1 --file /host/var/log/auth.log
```

The image is published for Intel and ARM servers in Amazon's public registry, so no sign-in is needed to pull it.
The token goes in the `WH_TOKEN` setting, never on the command line, where every user on the server could read it.
A customer who keeps images in their own registry can copy it there, or build it from `backend/agent/Dockerfile`.
WhiteHats publishes a new version with `deploy/collector/publish.sh`.

### Linux service (systemd)

The generated unit starts with the install steps.
As root, fetch the program from your WhiteHats server, check its fingerprint, and install `psutil`:

```sh
mkdir -p /opt/whitehats
curl -fsSL https://whitehats.example.com/api/agent/collector.py -o /opt/whitehats/collector.py
echo '<fingerprint from the unit>  /opt/whitehats/collector.py' | sha256sum -c
chmod 0755 /opt/whitehats && chmod 0644 /opt/whitehats/collector.py   # the service runs as its own user
python3 -m pip install psutil==6.1.1   # or the system's python3-psutil package
```

Then save the generated unit as `/etc/systemd/system/whitehats-network-collector.service` (the name it gives), and:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now whitehats-network-collector
journalctl -u whitehats-network-collector -f
```

The unit uses a dynamic user, a read-only system, a private `/tmp`, a 256 MB memory limit and 25% CPU.

## Staying inside the AI's context window

No model ever reads raw collector data.

1. Each log line is reduced to its shape by masking numbers, IDs and paths. Millions of lines become hundreds of shapes with counts.
2. Each shape is scored against its own 24-hour baseline: new, spiking, failing or security-related shapes rise.
3. Each collector's notable shapes are packed into **its own token budget**, set when you deploy it. Then all collectors are packed into the digest's total budget.

Raise a collector's budget for a critical system; lower it for noisy, low-value hosts.

## Health

| State | Meaning |
| --- | --- |
| online | Reported within two intervals |
| late | Missed a few reports |
| offline | Silent for a long time: a blind spot, shown on the CEO's overview |
| crashed | A local collector's process exited; its log file is shown |
| stopped | Stopped from the console; its token is refused |

Rotating a token stops the old one immediately.
