"""The framework's own tables, present in every site database.

They are created with the site (and again by `migrate` if missing). The schema sync
engine only manages tab_* tables and never touches these.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, Text, func, inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import Connection, Engine

system_metadata = MetaData()

# One row per DocType; `definition` is the full JSON that the builder edits.
sys_doctype = Table(
    "sys_doctype",
    system_metadata,
    Column("name", String(63), primary_key=True),
    Column("module", String(140), nullable=False),
    Column("definition", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("modified_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Guard 3: every DDL statement the sync engine ran, grouped per save by batch_id.
sys_schema_log = Table(
    "sys_schema_log",
    system_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("batch_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", String(140), nullable=False),
    Column("table_name", String(63), nullable=False),
    Column("statement", Text, nullable=False),
)

# Guard 4: data copied out right before a column or table is dropped.
sys_archive = Table(
    "sys_archive",
    system_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("batch_id", UUID(as_uuid=True), nullable=False, index=True),
    Column("archived_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("actor", String(140), nullable=False),
    Column("table_name", String(63), nullable=False),
    Column("column_name", String(63)),  # NULL when the whole table was dropped
    Column("row_id", BigInteger, nullable=False),
    Column("value", JSONB),  # the column's value, or the whole row for a dropped table
)


# Counters for autoname "format:" patterns, one row per series, e.g. "INV-2026-#".
sys_series = Table(
    "sys_series",
    system_metadata,
    Column("series", String(140), primary_key=True),
    Column("current", BigInteger, nullable=False),
)


def install_system_tables(bind: Engine | Connection) -> list[str]:
    """Create any missing system tables and return their names. Safe to run repeatedly."""
    existing = set(inspect(bind).get_table_names())
    system_metadata.create_all(bind)
    return [name for name in system_metadata.tables if name not in existing]
