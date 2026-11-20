# Adding a WhiteHat

A WhiteHat is one small Python class.
It says which data it needs, implements one or more hooks, and raises signals.
It never calls an AI model and never changes a customer system.

## 1. Write the class

Add it to `backend/app/whitehats/builtin.py`, or create a new module in that package:

```python
from .base import Context, WhiteHat


class DatabaseAccess(WhiteHat):
    key, name = "database", "Database"
    domain = "Query patterns, access anomalies and data exfiltration"
    description = "Flags database dumps and mass reads seen in database logs."
    data_sources = ("logs",)

    def on_log_batch(self, ctx: Context, source_host: str, lines: list[dict]) -> None:
        dumps = [ln for ln in lines if "pg_dump" in str(ln.get("message", ""))]
        if dumps:
            ctx.signal("high", f"Database dump started on {source_host}",
                       f"{len(dumps)} line(s). Example: {dumps[0]['message'][:200]}",
                       entity=source_host, mitre="T1005")
```

## 2. Register it

Add an instance to `BUILTIN` at the bottom of `builtin.py`.
If a planned entry with the same key exists in `PLANNED`, remove it.
The catalog is built from both lists in `app/whitehats/__init__.py`.

## Hooks

| Hook | Called with | When |
| --- | --- | --- |
| `on_log_batch(ctx, source_host, lines)` | Raw lines: `{"source", "message"}` | Every batch from a log collector or cloud connector |
| `on_log_window(ctx, items)` | Scored shapes: pattern, count, level, security, new, spike, reasons | Each time a digest is built |
| `on_network_findings(ctx, host, new_findings)` | Network findings that just appeared | Each network snapshot |
| `on_processes(ctx, host, processes)` | `{"name", "pid", "user", "cpu", "memory_mb"}` | Each process snapshot |

Hooks run only for tenants that have the WhiteHat switched on (not *dark*).

## Raising signals

`ctx.signal(severity, title, detail, entity, mitre, dedupe_hours=6)`:

- `severity`: `critical`, `high`, `medium` or `low`. Critical and high also send an alert.
- The same title is raised at most once per `dedupe_hours`, so a noisy source does not flood the console.
- Write titles a CEO understands. Put the technical detail in `detail`.

## Data sources

Declare what the WhiteHat needs in `data_sources` using keys from `DATA_SOURCES` in `base.py` (`logs`, `network`, `processes`, `cloud_audit`, `code`, `identity`).
The console shows these so a customer knows what to connect.
If a WhiteHat needs a source that does not exist yet, keep it in `PLANNED` with `available = False` and an `unavailable_reason`; it is listed but cannot be switched on or sold.

## 3. Test it

Add a test in `backend/tests/` that sends data through the real ingest endpoint and checks the signal:

```python
def test_database_dump_is_flagged(client, ceo):
    out = client.post("/api/collectors", headers=ceo,
                      json={"name": "db logs", "kind": "log", "mode": "docker"}).json()
    tok = {"Authorization": f"Bearer {out['token']}"}
    client.post("/api/ingest/logs", headers=tok,
                json={"host": "db-1", "lines": [{"source": "pg", "message": "pg_dump started"}]})
    assert any("Database dump" in s["title"] for s in client.get("/api/signals", headers=ceo).json())
```

## 4. Switch it on

An Engineer or Project admin switches it on under *WhiteHats → Add from catalog*.
Operators can include it in a new company's contracted WhiteHats; the readiness check then requires it to be on.
