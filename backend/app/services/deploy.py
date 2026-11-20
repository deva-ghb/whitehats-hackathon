"""Deploying collectors: local processes we manage, and manifests for Docker, Kubernetes and systemd."""

from __future__ import annotations

import hashlib
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

import psutil
import yaml

from ..config import BACKEND_DIR, settings
from ..db import utcnow
from ..models import Collector

AGENT_SCRIPT = BACKEND_DIR / "agent" / "collector.py"
AGENT_VERSION = "1.0.0"
# Published for both Intel and ARM servers (deploy/collector/publish.sh); a different registry can be named.
AGENT_IMAGE = os.environ.get("WH_COLLECTOR_IMAGE", f"public.ecr.aws/f8g3u1i7/whitehats-collector:{AGENT_VERSION}")

KIND_LABEL = {"log": "Log collector", "network": "Network analyzer", "process": "Process monitor"}


def agent_sha256() -> str:
    """The collector program's fingerprint, so a server that downloads it can check it is the one WhiteHats serves."""
    return hashlib.sha256(Path(AGENT_SCRIPT).read_bytes()).hexdigest()


def default_config(kind: str) -> dict:
    if kind == "log":
        candidates = [str(settings.log_dir / "platform.log"), "/var/log/system.log", "/var/log/syslog",
                      "/var/log/auth.log", "/var/log/install.log"]
        return {"files": [p for p in candidates if os.access(p, os.R_OK)] or candidates[:1], "backfill_lines": 2000}
    if kind == "network":
        return {"include_listening": True}
    return {"top": 25}


def agent_args(c: Collector, token: str, server: str) -> list[str]:
    """The collector's arguments. The token is passed as an argument only to a collector this server starts itself;
    the files for other servers hand it over in the WH_TOKEN setting, out of every other user's process list."""
    args = ["--server", server, *(["--token", token] if token else []), "--kind", c.kind,
            "--interval", str(c.interval_s), "--name", c.name]
    for f in c.config.get("files", []) if c.kind == "log" else []:
        args += ["--file", f]
    if c.kind == "log" and c.config.get("backfill_lines"):
        args += ["--backfill", str(c.config["backfill_lines"])]
    return args


# ------------------------------------------------------------------ local processes


# Collectors this server started, by pid, so a stopped one is waited for instead of left defunct.
# A collector started by an earlier server run is stopped by pid alone.
_started: dict[int, subprocess.Popen] = {}
STOP_WAIT_S = 5


def start_local(c: Collector, token: str) -> int:
    # The child keeps its own copy of the log file; the server's copy closes here.
    with (settings.log_dir / f"collector-{c.id}.log").open("a") as log:
        proc = subprocess.Popen(
            [sys.executable, str(AGENT_SCRIPT), *agent_args(c, token, settings.public_url)],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    _started[proc.pid] = proc
    c.pid, c.status, c.version = proc.pid, "running", AGENT_VERSION
    return proc.pid


def is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE and "collector.py" in " ".join(p.cmdline())
    except (psutil.Error, OSError):
        return False


def stop_local(c: Collector) -> bool:
    stopped = False
    if is_alive(c.pid):
        try:
            os.kill(c.pid, signal.SIGTERM)
            stopped = True
        except OSError:
            pass
    proc = _started.pop(c.pid, None) if c.pid else None
    if proc is not None:
        _wait_for_exit(proc)
    c.status, c.pid = "stopped", None
    return stopped


def _wait_for_exit(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=STOP_WAIT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def health(c: Collector) -> str:
    """online | late | offline | stopped | pending, from heartbeats and the process table."""
    if c.status == "stopped":
        return "stopped"
    if c.status == "crashed":
        return "crashed"
    if c.last_heartbeat is None:
        return "pending"
    age = (utcnow() - c.last_heartbeat).total_seconds()
    if age <= c.interval_s * 2 + 10:
        return "online"
    if age <= c.interval_s * 6 + 30:
        return "late"
    return "offline"


# ------------------------------------------------------------------ manifests


def manifests(c: Collector, token_placeholder: str = "<paste-collector-token>") -> dict[str, str]:
    name = f"whitehats-{c.kind}-collector"
    args = agent_args(c, "", settings.public_url)
    # In containers, read the host's logs from the mounted path.
    if c.kind == "log":
        args = [a if not a.startswith("/var/log") else "/host" + a for a in args]
    docker_flags = ["--name", name, "-e", f"WH_TOKEN={token_placeholder}", "--restart", "unless-stopped", "--read-only",
                    "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--memory", "256m", "--cpus", "0.25"]
    if c.kind == "log":
        docker_flags += ["-v", "/var/log:/host/var/log:ro"]
    if c.kind in ("network", "process"):
        docker_flags += ["--network", "host", "--pid", "host"]
    quoted = " ".join(shlex.quote(a) for a in args)  # a name with a space stays one argument
    docker = "docker run -d \\\n  " + " \\\n  ".join(
        [" ".join(shlex.quote(f) for f in docker_flags[i:i + 2]) for i in range(0, len(docker_flags), 2)]
        + [AGENT_IMAGE + " \\\n  " + quoted])

    secret = {
        "apiVersion": "v1", "kind": "Secret", "metadata": {"name": f"{name}-token", "namespace": "whitehats"},
        "type": "Opaque", "stringData": {"token": token_placeholder},
    }
    k8s_args = list(args)  # the token comes from the secret, in WH_TOKEN
    container = {
        "name": "collector", "image": AGENT_IMAGE, "args": k8s_args,
        "env": [{"name": "WH_TOKEN", "valueFrom": {"secretKeyRef": {"name": f"{name}-token", "key": "token"}}}],
        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "250m", "memory": "256Mi"}},
        "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                            "runAsNonRoot": c.kind == "log", "capabilities": {"drop": ["ALL"]}},
    }
    pod_spec: dict = {"serviceAccountName": "whitehats-collector", "containers": [container],
                      "tolerations": [{"operator": "Exists"}]}
    if c.kind == "log":
        container["volumeMounts"] = [{"name": "varlog", "mountPath": "/host/var/log", "readOnly": True}]
        pod_spec["volumes"] = [{"name": "varlog", "hostPath": {"path": "/var/log"}}]
    else:
        pod_spec["hostNetwork"], pod_spec["hostPID"] = True, True
    daemonset = {
        "apiVersion": "apps/v1", "kind": "DaemonSet",
        "metadata": {"name": name, "namespace": "whitehats", "labels": {"app": name}},
        "spec": {"selector": {"matchLabels": {"app": name}},
                 "template": {"metadata": {"labels": {"app": name}}, "spec": pod_spec}},
    }
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "whitehats"}}
    sa = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "whitehats-collector",
                                                                      "namespace": "whitehats"},
          "automountServiceAccountToken": False}
    netpol = {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": f"{name}-egress", "namespace": "whitehats"},
        "spec": {"podSelector": {"matchLabels": {"app": name}}, "policyTypes": ["Ingress", "Egress"],
                 "ingress": [], "egress": [{"ports": [{"port": 443, "protocol": "TCP"},
                                                      {"port": 53, "protocol": "UDP"}]}]},
    }
    k8s = "---\n".join(yaml.safe_dump(d, sort_keys=False) for d in (namespace, sa, secret, netpol, daemonset))

    script = f"{settings.public_url.rstrip('/')}/api/agent/collector.py"
    systemd = (
        "# Install once, as root:\n"
        "#   mkdir -p /opt/whitehats\n"
        f"#   curl -fsSL {script} -o /opt/whitehats/collector.py\n"
        f"#   echo '{agent_sha256()}  /opt/whitehats/collector.py' | sha256sum -c\n"
        "#   chmod 0755 /opt/whitehats && chmod 0644 /opt/whitehats/collector.py   (the service runs as its own user)\n"
        "#   python3 -m pip install psutil==6.1.1   (or your system's python3-psutil package)\n"
        f"# Save this file as /etc/systemd/system/{name}.service, then: systemctl enable --now {name}\n\n"
        f"[Unit]\nDescription=WhiteHats {KIND_LABEL[c.kind]}\nAfter=network-online.target\n\n"
        f"[Service]\nEnvironment=\"WH_TOKEN={token_placeholder}\"\n"
        f"ExecStart=/usr/bin/python3 /opt/whitehats/collector.py {quoted}\n"
        "Restart=always\nRestartSec=10\nDynamicUser=yes\nNoNewPrivileges=yes\nProtectSystem=strict\n"
        "ProtectHome=yes\nPrivateTmp=yes\nMemoryMax=256M\nCPUQuota=25%\n"
        + ("SupplementaryGroups=adm\n" if c.kind == "log" else "")
        + "\n[Install]\nWantedBy=multi-user.target\n"
    )
    return {"docker": docker, "kubernetes": k8s, "systemd": systemd,
            "dockerfile": (Path(AGENT_SCRIPT).parent / "Dockerfile").read_text()}
