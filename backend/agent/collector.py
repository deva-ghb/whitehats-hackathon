#!/usr/bin/env python3
"""WhiteHats collector agent.

One small program, three jobs, chosen with --kind:
  log      tail log files and ship new lines in batches
  network  snapshot live connections and listening ports
  process  snapshot the busiest processes

It only reads. It only talks to the WhiteHats server, over HTTP(S), with its own
token. It needs nothing beyond the Python standard library and psutil.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from collections import deque

VERSION = "1.0.0"
MAX_BATCH = 2000
RUNNING = True


def _stop(*_):
    global RUNNING
    RUNNING = False


def post(server: str, token: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        server.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 "User-Agent": f"whitehats-collector/{VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read() or b"{}")


class Tailer:
    def __init__(self, paths: list[str], backfill: int):
        self.files: dict[str, tuple[int, int]] = {}  # path -> (inode, offset)
        self.pending: list[dict] = []
        for p in paths:
            self._open(p, backfill)

    def _open(self, path: str, backfill: int) -> None:
        try:
            st = os.stat(path)
        except OSError:
            print(f"cannot read {path}, skipping", flush=True)
            return
        offset = st.st_size
        if backfill:
            with open(path, "rb") as f:
                tail = deque(f, maxlen=backfill)
            self.pending += [{"source": path, "message": line.decode(errors="replace").rstrip()} for line in tail]
        self.files[path] = (st.st_ino, offset)

    def read(self) -> list[dict]:
        out, self.pending = self.pending, []
        for path, (inode, offset) in list(self.files.items()):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_ino != inode or st.st_size < offset:  # rotated or truncated
                offset = 0
            if st.st_size == offset:
                self.files[path] = (st.st_ino, offset)
                continue
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(4_000_000)
                offset = f.tell()
            for line in data.decode(errors="replace").splitlines():
                if line.strip():
                    out.append({"source": path, "message": line})
            self.files[path] = (st.st_ino, offset)
        return out


def snapshot_network() -> list[dict]:
    import psutil

    rows = []  # (conn, pid, process name)
    try:
        for c in psutil.net_connections(kind="inet"):
            rows.append((c, c.pid or 0, None))
    except (psutil.AccessDenied, PermissionError):
        # Without admin rights (for example on macOS), read the connections of processes we can see.
        for p in psutil.process_iter(["pid", "name"]):
            try:
                for c in p.net_connections(kind="inet"):
                    rows.append((c, p.pid, p.info["name"] or ""))
            except (psutil.Error, OSError):
                continue
    names: dict[int, str] = {}
    conns, seen = [], set()
    for c, pid, name in rows:
        laddr = c.laddr.ip if c.laddr else ""
        lport = c.laddr.port if c.laddr else 0
        raddr = c.raddr.ip if c.raddr else ""
        rport = c.raddr.port if c.raddr else 0
        key = (laddr, lport, raddr, rport, c.status, pid)
        if key in seen:
            continue
        seen.add(key)
        if name is None and pid:
            if pid not in names:
                try:
                    names[pid] = psutil.Process(pid).name()
                except (psutil.Error, OSError):
                    names[pid] = ""
            name = names[pid]
        conns.append({"proto": "tcp" if c.type == socket.SOCK_STREAM else "udp", "laddr": laddr, "lport": lport,
                      "raddr": raddr, "rport": rport, "state": c.status or "NONE", "pid": pid,
                      "process": name or ""})
    return conns


def snapshot_processes(top: int) -> list[dict]:
    import psutil

    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            cpu = p.cpu_percent(interval=None)
            mem = p.info["memory_info"].rss / 1_048_576 if p.info["memory_info"] else 0
            procs.append({"pid": p.info["pid"], "name": p.info["name"] or "", "user": p.info["username"] or "",
                          "cpu": cpu, "memory_mb": round(mem, 1)})
        except (psutil.Error, OSError):
            continue
    procs.sort(key=lambda x: (x["cpu"], x["memory_mb"]), reverse=True)
    return procs[:top]


def main() -> int:
    ap = argparse.ArgumentParser(description="WhiteHats collector agent")
    ap.add_argument("--server", required=True)
    ap.add_argument("--token", default=os.environ.get("WH_TOKEN", ""))
    ap.add_argument("--kind", choices=["log", "network", "process"], required=True)
    ap.add_argument("--interval", type=int, default=15)
    ap.add_argument("--name", default=socket.gethostname())
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    if not args.token:
        print("a token is required (--token or WH_TOKEN)", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    host = socket.gethostname()
    tailer = Tailer(args.file, args.backfill) if args.kind == "log" else None
    if args.kind == "process":
        import psutil

        for p in psutil.process_iter():
            try:
                p.cpu_percent(interval=None)  # prime CPU counters
            except (psutil.Error, OSError):
                pass
    print(f"collector {args.name} ({args.kind}) v{VERSION} reporting to {args.server}", flush=True)
    backoff = 1
    while RUNNING:
        started = time.monotonic()
        try:
            if args.kind == "log":
                lines = tailer.read()
                for i in range(0, max(len(lines), 1), MAX_BATCH):
                    post(args.server, args.token, "/api/ingest/logs",
                         {"host": host, "version": VERSION, "lines": lines[i:i + MAX_BATCH]})
            elif args.kind == "network":
                post(args.server, args.token, "/api/ingest/network",
                     {"host": host, "version": VERSION, "connections": snapshot_network()})
            else:
                post(args.server, args.token, "/api/ingest/processes",
                     {"host": host, "version": VERSION, "processes": snapshot_processes(args.top)})
            backoff = 1
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                print("server rejected this collector's token; stopping", flush=True)
                return 3
            print(f"server error {exc.code}; retrying", flush=True)
            backoff = min(backoff * 2, 60)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"cannot reach server ({exc}); retrying in {backoff}s", flush=True)
            backoff = min(backoff * 2, 60)
        except Exception as exc:  # noqa: BLE001 - a collector must keep running whatever one snapshot does
            print(f"snapshot failed ({type(exc).__name__}: {exc}); retrying in {backoff}s", flush=True)
            backoff = min(backoff * 2, 60)
        wait = max(args.interval - (time.monotonic() - started), 1) if backoff == 1 else backoff
        end = time.monotonic() + wait
        while RUNNING and time.monotonic() < end:
            time.sleep(0.5)
    print("collector stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
