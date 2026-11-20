"""The knowledge sphere's graph, in PostgreSQL with the Apache AGE extension (KS.1).

One AGE graph per project, named ks_p<project id>. What the sphere knows now lives here, queried in openCypher; its
history lives beside it in the platform's own tables (app/sphere/store.py), so any version can be rebuilt and the
graph itself can be rebuilt from that history.

Nodes carry a `key` (stable across versions) and their kind as the label: Concept, Rule or Exception. Relationships:
ABOUT (a rule or exception is about a concept), EXEMPTS (an exception is where a rule deliberately does not apply),
RELATES (one concept to another). Every value goes in as a query parameter, never into the query text.

The connection string is WH_SPHERE_DATABASE_URL. Without it the sphere is off: nothing is written or read, and reviews
go on without it.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from typing import Self

LABELS = ("Concept", "Rule", "Exception")
EDGES = ("ABOUT", "EXEMPTS", "RELATES")
_lock = threading.Lock()
_oid: int | None = None


class Unavailable(Exception):
    """The sphere's database is not configured or cannot be reached."""


def url() -> str:
    return os.environ.get("WH_SPHERE_DATABASE_URL", "").strip()


def configured() -> bool:
    return bool(url())


def graph_name(project_id: int) -> str:
    return f"ks_p{int(project_id)}"


class _Params(dict):
    """A map passed to cypher() as its agtype parameter."""


def _connect():
    global _oid
    if not configured():
        raise Unavailable("The knowledge sphere's database is not configured.")
    import psycopg
    from psycopg.adapt import Dumper

    try:
        conn = psycopg.connect(url().replace("postgresql+psycopg://", "postgresql://"), autocommit=True,
                               connect_timeout=5)
    except psycopg.OperationalError as exc:
        raise Unavailable(f"The knowledge sphere's database cannot be reached: {exc}") from exc
    conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    conn.execute("LOAD 'age'")
    conn.execute('SET search_path = ag_catalog, "$user", public')
    if _oid is None:
        row = conn.execute("SELECT oid FROM pg_type WHERE typname = 'agtype'").fetchone()
        if row is None:
            raise Unavailable("The knowledge sphere's database has no AGE extension.")
        _oid = int(row[0])

    class AgDumper(Dumper):
        oid = _oid or 0

        def dump(self, obj):
            return json.dumps(obj).encode()

    conn.adapters.register_dumper(_Params, AgDumper)
    return conn


def _agtype(value: str):
    """An agtype value as Python: vertices and edges carry a ::vertex / ::edge suffix."""
    text = str(value)
    for suffix in ("::vertex", "::edge", "::path"):
        text = text.removesuffix(suffix)
    return json.loads(text)


def _sets(var: str, props: dict) -> tuple[str, dict]:
    """A SET list naming each property, with its value as a parameter. AGE takes no map parameter in SET. Property names
    come from this package's own code; anything that is not a plain name is refused."""
    parts, params = [], {}
    for i, (name, value) in enumerate(sorted(props.items())):
        if not name.isidentifier():
            raise ValueError(f"Not a property name: {name!r}")
        parts.append(f"{var}.{name} = $v{i}")
        params[f"v{i}"] = value
    return ", ".join(parts), params


class Graph:
    """One project's graph. Use as a context manager: one connection per unit of work."""

    def __init__(self, project_id: int):
        self.name = graph_name(project_id)
        self.conn = None

    def __enter__(self) -> Self:
        self.conn = _connect()
        assert self.conn is not None
        with _lock:
            if not self.conn.execute("SELECT 1 FROM ag_graph WHERE name = %s", (self.name,)).fetchone():
                self.conn.execute("SELECT create_graph(%s)", (self.name,))
        return self

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            self.conn.close()

    def _cypher(self, query: str, params: dict | None = None, columns: str = "r agtype") -> list[tuple]:
        from psycopg import sql

        if self.conn is None:
            raise Unavailable("Use the graph inside a with block.")
        # The query text is this module's own (labels from fixed lists); every value goes in as the parameter. The
        # graph name is quoted as a literal: AGE cannot take it as a parameter.
        body = query.replace("{", "{{").replace("}", "}}")  # openCypher's maps, not format fields
        text = sql.SQL("SELECT * FROM cypher({}, $$ " + body + " $$, %s) AS (" + columns + ")")  # type: ignore[arg-type]
        return self.conn.execute(text.format(sql.Literal(self.name)), (_Params(params or {}),)).fetchall()

    def put_node(self, label: str, key: str, props: dict) -> None:
        """Create the node or replace its properties (its key and label stay)."""
        if label not in LABELS:
            raise ValueError(f"Unknown node kind {label}")
        sets, params = _sets("n", {**props, "key": key})
        self._cypher(f"MERGE (n:{label} {{key: $key}}) SET {sets} RETURN 0", params)

    def drop_node(self, key: str) -> None:
        self._cypher("MATCH (n {key: $key}) DETACH DELETE n RETURN 0", {"key": key})

    def put_edge(self, kind: str, source: str, target: str, props: dict | None = None) -> None:
        if kind not in EDGES:
            raise ValueError(f"Unknown relationship {kind}")
        sets, params = _sets("e", props or {"since": ""})
        self._cypher(f"MATCH (a {{key: $source}}), (b {{key: $target}}) MERGE (a)-[e:{kind}]->(b) SET {sets} "
                     "RETURN 0", {**params, "source": source, "target": target})

    def drop_edge(self, kind: str, source: str, target: str) -> None:
        if kind not in EDGES:
            raise ValueError(f"Unknown relationship {kind}")
        self._cypher(f"MATCH (a {{key: $source}})-[e:{kind}]->(b {{key: $target}}) DELETE e RETURN 0",
                     {"source": source, "target": target})

    def clear(self) -> None:
        self._cypher("MATCH (n) DETACH DELETE n RETURN 0")

    def nodes(self) -> list[dict]:
        rows = self._cypher("MATCH (n) RETURN label(n), properties(n)", columns="l agtype, p agtype")
        return [{**_agtype(p), "kind": _agtype(label)} for label, p in rows]

    def edges(self) -> list[dict]:
        rows = self._cypher("MATCH (a)-[e]->(b) RETURN type(e), a.key, b.key, properties(e)",
                            columns="t agtype, s agtype, d agtype, p agtype")
        return [{"kind": _agtype(t), "source": _agtype(s), "target": _agtype(d), **_agtype(p)} for t, s, d, p in rows]

    def around(self, keys: Iterable[str]) -> list[dict]:
        """The nodes given, everything one step from them (a rule's concept and its exceptions), and the other rules and
        exceptions about those concepts (what a review of a change touching those nodes should keep in mind)."""
        keys = sorted(set(keys))
        if not keys:
            return []
        rows = self._cypher("MATCH (n) WHERE n.key IN $keys OPTIONAL MATCH (n)-[]-(d) "
                            "OPTIONAL MATCH (d)-[]-(m) WHERE label(d) = 'Concept' "
                            "RETURN collect(DISTINCT n.key) + collect(DISTINCT d.key) + collect(DISTINCT m.key)",
                            {"keys": keys})
        found = {k for row in rows for k in (_agtype(row[0]) or []) if k}
        return [n for n in self.nodes() if n["key"] in found]
