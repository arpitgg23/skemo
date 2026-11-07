"""Schema sync engine: makes a site's tab_* tables match its DocType metadata.

Alembic's autogenerate does the diffing (wanted tables vs the live database). Around it,
this module adds four guards, and it always runs inside the caller's transaction:

1. One transaction: Postgres DDL is transactional, so if any step fails, every change in
   the save is rolled back. Nothing is left half-applied.
2. Explicit renames: a renamed field becomes RENAME COLUMN. The diff alone can't tell a
   rename from a drop + add, and would lose the column's data.
3. Schema log: every DDL statement is written to sys_schema_log.
4. Archive before drop: data is copied to sys_archive right before DROP COLUMN/TABLE.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from alembic.autogenerate import produce_migrations
from alembic.operations import Operations, ops
from alembic.runtime.migration import MigrationContext
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    event,
    func,
    insert,
    inspect,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.types import TypeEngine

from .fieldtypes import FIELD_TYPES
from .meta import TABLE_PREFIX, DocType
from .system_tables import sys_schema_log

# Predictable names mean the names Postgres reports back match the ones we generate, so
# re-running the diff finds nothing to change.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
}

# Within one table: drop indexes and constraints before the columns they cover, and add
# columns before the indexes and constraints that need them.
_OP_ORDER = {
    ops.DropIndexOp: 0,
    ops.DropConstraintOp: 0,
    ops.DropColumnOp: 1,
    ops.AlterColumnOp: 2,
    ops.AddColumnOp: 3,
    ops.CreateIndexOp: 4,
    ops.CreateUniqueConstraintOp: 4,
}

Renames = Mapping[str, Mapping[str, str]]  # DocType name -> {old fieldname: new fieldname}


class SchemaSyncError(Exception):
    """A schema change could not be applied. The caller's transaction rolls back."""


@dataclass
class SyncResult:
    batch_id: uuid.UUID
    statements: list[str] = field(default_factory=list)
    archived_rows: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.statements)


def build_table(doctype: DocType, metadata: MetaData) -> Table:
    """The table a DocType should have: standard columns, then one per data field.

    A child DocType's rows belong to a record instead of standing on their own, so they
    carry which record and which field they're under, and their position in the grid.
    """
    columns = (
        [
            Column("id", BigInteger, primary_key=True),
            Column("parent", String(140), nullable=False),
            Column("parenttype", String(63), nullable=False),
            Column("parentfield", String(63), nullable=False),
            Column("idx", Integer, nullable=False),
            Index(
                f"ix_{doctype.table_name}_parent",
                "parenttype",
                "parent",
                "parentfield",
            ),
        ]
        if doctype.is_child
        else [
            Column("id", BigInteger, primary_key=True),
            Column("name", String(140), nullable=False, unique=True),
            Column("owner", String(140)),
            Column("created_at", DateTime(timezone=True), server_default=func.now()),
            Column("modified_at", DateTime(timezone=True), server_default=func.now()),
            Column("modified_by", String(140)),
        ]
    )
    for f in doctype.data_fields:
        # "Required" is checked by the API, not with NOT NULL, so toggling it needs no DDL.
        columns.append(
            Column(
                f.fieldname,
                FIELD_TYPES[f.fieldtype].column_type(f),
                unique=f.unique,
                index=f.search_index and not f.unique,
            )
        )
    return Table(doctype.table_name, metadata, *columns)


def build_metadata(doctypes: Iterable[DocType]) -> MetaData:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    for doctype in doctypes:
        build_table(doctype, metadata)
    return metadata


def sync_schema(
    conn: Connection,
    doctypes: Iterable[DocType],
    *,
    renames: Renames | None = None,
    actor: str = "Administrator",
) -> SyncResult:
    """Make the tab_* tables match `doctypes`, which must be the site's complete set.

    Tables whose DocType is missing from `doctypes` are dropped after their rows are
    archived. Run it inside a transaction: on SchemaSyncError the caller rolls back.
    """
    result = SyncResult(batch_id=uuid.uuid4())
    recorder = _DDLRecorder()
    context = MigrationContext.configure(
        conn, opts={"compare_type": True, "include_name": _is_sync_table}
    )
    operations = Operations(context)

    event.listen(conn, "before_cursor_execute", recorder.record)
    try:
        _rename_columns(conn, operations, recorder, renames or {})
        diff = produce_migrations(context, build_metadata(doctypes))
        for op in _flatten(diff.upgrade_ops):
            table = getattr(op, "table_name", "")
            recorder.table = table
            if isinstance(op, ops.DropTableOp):
                result.archived_rows += _archive_table(conn, table, result.batch_id, actor)
            elif isinstance(op, ops.DropColumnOp):
                result.archived_rows += _archive_column(
                    conn, table, op.column_name, result.batch_id, actor
                )
            elif isinstance(op, ops.AlterColumnOp) and op.modify_type is not None:
                # Postgres converts the existing values; if any can't be converted it
                # raises, and the whole save rolls back.
                op.kw["postgresql_using"] = _cast(conn, op.column_name, op.modify_type)
            operations.invoke(op)
    except DBAPIError as e:
        raise SchemaSyncError(f"{recorder.table}: {_first_line(e.orig or e)}") from e
    finally:
        event.remove(conn, "before_cursor_execute", recorder.record)

    if recorder.entries:
        conn.execute(
            insert(sys_schema_log),
            [
                {"batch_id": result.batch_id, "actor": actor, "table_name": t, "statement": s}
                for t, s in recorder.entries
            ],
        )
    result.statements = [s for _, s in recorder.entries]
    return result


class _DDLRecorder:
    """Collects DDL statements as they run, tagged with the table being changed."""

    def __init__(self) -> None:
        self.table = ""
        self.entries: list[tuple[str, str]] = []

    def record(self, conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")):
            self.entries.append((self.table, statement.strip()))


def _is_sync_table(name: str | None, type_: str, parent_names: object) -> bool:
    """Only tab_* tables belong to the engine; system and unrelated tables are left alone."""
    return type_ != "table" or (name or "").startswith(TABLE_PREFIX)


def _flatten(container: ops.OpContainer) -> Iterator[ops.MigrateOperation]:
    for op in container.ops:
        if isinstance(op, ops.ModifyTableOps):
            yield from sorted(op.ops, key=lambda o: _OP_ORDER.get(type(o), 5))
        elif isinstance(op, ops.OpContainer):
            yield from _flatten(op)
        else:
            yield op


def _rename_columns(
    conn: Connection, operations: Operations, recorder: _DDLRecorder, renames: Renames
) -> None:
    inspector = inspect(conn)
    for doctype, mapping in renames.items():
        if not mapping:
            continue
        table = TABLE_PREFIX + doctype
        if not inspector.has_table(table):
            raise SchemaSyncError(f"{table}: can't rename columns of a table that doesn't exist")
        existing = {c["name"] for c in inspector.get_columns(table)}
        for old, new in mapping.items():
            if old not in existing:
                raise SchemaSyncError(f"{table}: can't rename {old!r}, there is no such column")
            if new in existing:
                raise SchemaSyncError(f"{table}: can't rename {old!r} to {new!r}, it exists")
            recorder.table = table
            operations.alter_column(table, old, new_column_name=new)
            existing = (existing - {old}) | {new}


def _archive_column(
    conn: Connection, table: str, column: str, batch_id: uuid.UUID, actor: str
) -> int:
    q = conn.dialect.identifier_preparer.quote
    sql = text(
        "INSERT INTO sys_archive (batch_id, actor, table_name, column_name, row_id, value) "
        f"SELECT :batch_id, :actor, :table, :column, id, to_jsonb({q(column)}) "
        f"FROM {q(table)} WHERE {q(column)} IS NOT NULL"
    )
    params = {"batch_id": batch_id, "actor": actor, "table": table, "column": column}
    return conn.execute(sql, params).rowcount


def _archive_table(conn: Connection, table: str, batch_id: uuid.UUID, actor: str) -> int:
    q = conn.dialect.identifier_preparer.quote
    sql = text(
        "INSERT INTO sys_archive (batch_id, actor, table_name, row_id, value) "
        f"SELECT :batch_id, :actor, :table, t.id, to_jsonb(t) FROM {q(table)} AS t"
    )
    return conn.execute(sql, {"batch_id": batch_id, "actor": actor, "table": table}).rowcount


def _cast(conn: Connection, column: str, type_: TypeEngine) -> str:
    quoted = conn.dialect.identifier_preparer.quote(column)
    return f"{quoted}::{type_.compile(dialect=conn.dialect)}"


def _first_line(error: BaseException) -> str:
    return str(error).strip().splitlines()[0]
