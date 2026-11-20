"""Bring an upgraded database's constraints and indexes in line with the models.

`ALTER TABLE ADD COLUMN` cannot add an index, a uniqueness rule or NOT NULL, so a
database that was upgraded rather than created fresh keeps its old rules: the
project_id indexes are missing and uniqueness is still per account, not per
project. This compares each table with its model through SQLAlchemy's inspector,
which reads SQLite and Postgres alike, and fixes what differs. Postgres can alter
a table in place; SQLite cannot drop a constraint, so the table is rebuilt.

Nothing is dropped: a column the models no longer have is carried over, made
nullable so new rows do not need it, and NOT NULL is only applied to a column
that holds no nulls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import Column, MetaData, Table, UniqueConstraint, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.interfaces import ReflectedColumn
from sqlalchemy.schema import CreateTable

from .db import Base


@dataclass
class TableDrift:
    """What one table lacks or has extra compared with its model."""

    name: str
    extra_unique: list[tuple[str, ...]] = field(default_factory=list)
    missing_unique: list[tuple[str, ...]] = field(default_factory=list)
    tighten: list[str] = field(default_factory=list)  # model says NOT NULL, the data allows it
    loosen: list[str] = field(default_factory=list)  # model allows null, the database refuses it
    stale_default: list[str] = field(default_factory=list)  # the database fills a value the model never sets

    @property
    def needs_change(self) -> bool:
        return bool(self.extra_unique or self.missing_unique or self.tighten or self.loosen or self.stale_default)


def match_models(engine: Engine) -> list[str]:
    """Fix uniqueness rules, NOT NULL and indexes on every model table that already exists."""
    done = []
    existing = set(inspect(engine).get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        with engine.connect() as conn:
            drift = table_drift(conn, table)
        if drift.needs_change:
            orphans = 0
            if engine.dialect.name == "sqlite":
                orphans = _rebuild_sqlite_table(engine, table, drift)
            else:
                _alter_in_place(engine, drift)
            done.append(_describe(drift))
            if orphans:
                done.append(f"kept {orphans} {table.name} rows that point at a missing parent")
        added = _create_missing_indexes(engine, table)
        if added:
            done.append(f"added {added} indexes to {table.name}")
    return done


def table_drift(conn: Connection, table: Table) -> TableDrift:
    insp = inspect(conn)
    drift = TableDrift(table.name)
    want = {_columns(uc) for uc in table.constraints if isinstance(uc, UniqueConstraint)}
    have = {tuple(uc["column_names"]) for uc in insp.get_unique_constraints(table.name)}
    drift.extra_unique = sorted(have - want)
    drift.missing_unique = sorted(want - have)
    model_columns = {col.name: col for col in table.columns}
    for reflected in insp.get_columns(table.name):
        col = model_columns.get(reflected["name"])
        if col is None or col.primary_key:
            continue
        if reflected.get("default") is not None and not _model_has_default(col):
            drift.stale_default.append(col.name)
        if col.nullable and not reflected["nullable"]:
            drift.loosen.append(col.name)
        elif not col.nullable and reflected["nullable"] and not _has_nulls(conn, table.name, col.name):
            drift.tighten.append(col.name)
    return drift


def _model_has_default(col: Column) -> bool:
    """migrate adds a scalar Python default as a database default, so both count."""
    if col.server_default is not None:
        return True
    return col.default is not None and not callable(getattr(col.default, "arg", None))


def _columns(constraint: UniqueConstraint) -> tuple[str, ...]:
    return tuple(col.name for col in constraint.columns)


def _has_nulls(conn: Connection, table: str, column: str) -> bool:
    quote = conn.dialect.identifier_preparer.quote
    found = conn.execute(text(f"SELECT 1 FROM {quote(table)} WHERE {quote(column)} IS NULL LIMIT 1")).first()
    return found is not None


def _describe(drift: TableDrift) -> str:
    parts = []
    if drift.extra_unique or drift.missing_unique:
        parts.append("uniqueness rules")
    if drift.tighten or drift.loosen:
        parts.append("required columns")
    if drift.stale_default:
        parts.append("default values")
    return f"matched {' and '.join(parts)} on {drift.name} to the models"


def _alter_in_place(engine: Engine, drift: TableDrift) -> None:
    with engine.begin() as conn:
        quote = conn.dialect.identifier_preparer.quote
        table = quote(drift.name)
        insp = inspect(conn)
        for uc in insp.get_unique_constraints(drift.name):
            if tuple(uc["column_names"]) in drift.extra_unique:
                conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT {quote(str(uc['name']))}"))
        for columns in drift.missing_unique:
            listed = ", ".join(quote(name) for name in columns)
            conn.execute(text(f"ALTER TABLE {table} ADD UNIQUE ({listed})"))
        for name in drift.tighten:
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {quote(name)} SET NOT NULL"))
        for name in drift.loosen:
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {quote(name)} DROP NOT NULL"))
        for name in drift.stale_default:
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {quote(name)} DROP DEFAULT"))


def _rebuild_sqlite_table(engine: Engine, table: Table, drift: TableDrift) -> int:
    """The documented SQLite procedure: create the new shape, copy, drop the old, rename.

    Foreign keys are switched off for the swap, which SQLite only allows outside a
    transaction, so the swap opens its own transaction after the pragma. Returns the
    rows that point at a missing parent. An old table without foreign keys could
    collect those; they are kept and reported rather than blocking start-up.
    """
    scratch = f"{table.name}__rebuild"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            conn.exec_driver_sql("BEGIN")
            try:
                reflected = inspect(conn).get_columns(table.name)
                shape = _rebuilt_shape(table, scratch, reflected, drift)
                copied = ", ".join(f'"{col["name"]}"' for col in reflected)
                conn.execute(text(f'DROP TABLE IF EXISTS "{scratch}"'))
                conn.execute(CreateTable(shape))
                conn.execute(text(f'INSERT INTO "{scratch}" ({copied}) SELECT {copied} FROM "{table.name}"'))
                conn.execute(text(f'DROP TABLE "{table.name}"'))
                conn.execute(text(f'ALTER TABLE "{scratch}" RENAME TO "{table.name}"'))
                orphans = len(conn.execute(text(f'PRAGMA foreign_key_check("{table.name}")')).all())
                conn.exec_driver_sql("COMMIT")
            except Exception:
                conn.exec_driver_sql("ROLLBACK")
                raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return orphans


def _rebuilt_shape(table: Table, scratch: str, reflected: Sequence[ReflectedColumn], drift: TableDrift) -> Table:
    """The model's table under a scratch name, with old columns kept and NOT NULL only where the data allows."""
    metadata = MetaData()
    for other in Base.metadata.sorted_tables:
        other.to_metadata(metadata)  # so foreign keys resolve
    shape = table.to_metadata(metadata, name=scratch)  # CreateTable leaves out indexes; they follow the rename
    allowed = set(drift.tighten)
    for col in shape.columns:
        if col.primary_key or col.nullable:
            continue
        was = next((item for item in reflected if item["name"] == col.name), None)
        if was is not None and was["nullable"] and col.name not in allowed:
            col.nullable = True
    for item in reflected:
        if item["name"] not in shape.columns:
            shape.append_column(Column(item["name"], item["type"], nullable=True))
    return shape


def _create_missing_indexes(engine: Engine, table: Table) -> int:
    with engine.begin() as conn:
        have = {index["name"] for index in inspect(conn).get_indexes(table.name)}
        missing = [index for index in table.indexes if index.name not in have]
        for index in missing:
            index.create(conn)
    return len(missing)
