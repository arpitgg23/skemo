"""The framework's own tables, present in every site database.

They are created with the site (and again by `migrate` if missing). The schema sync
engine only manages tab_* tables and never touches these.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    inspect,
)
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


# People who can sign in. `name` is the login, usually an email, and it's what `owner` and
# `modified_by` hold on every record.
sys_user = Table(
    "sys_user",
    system_metadata,
    Column("name", String(140), primary_key=True),
    Column("full_name", String(140), nullable=False, server_default=""),
    # scrypt, as "scrypt$<salt hex>$<hash hex>"; NULL means this user can't sign in yet.
    Column("password", Text),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_login_at", DateTime(timezone=True)),
)

sys_role = Table(
    "sys_role",
    system_metadata,
    Column("name", String(140), primary_key=True),
    Column("description", Text),
)

sys_user_role = Table(
    "sys_user_role",
    system_metadata,
    Column("user", String(140), ForeignKey("sys_user.name", ondelete="CASCADE"), primary_key=True),
    Column("role", String(140), ForeignKey("sys_role.name", ondelete="CASCADE"), primary_key=True),
)

# What a role may do with one DocType. No row means no access.
sys_permission = Table(
    "sys_permission",
    system_metadata,
    Column("id", BigInteger, primary_key=True),
    Column("doctype", String(63), nullable=False),
    Column("role", String(140), ForeignKey("sys_role.name", ondelete="CASCADE"), nullable=False),
    Column("can_read", Boolean, nullable=False, server_default="true"),
    Column("can_write", Boolean, nullable=False, server_default="false"),
    Column("can_create", Boolean, nullable=False, server_default="false"),
    Column("can_delete", Boolean, nullable=False, server_default="false"),
    # True: only records this user owns, so a sales rep sees only their own leads.
    Column("if_owner", Boolean, nullable=False, server_default="false"),
    UniqueConstraint("doctype", "role", name="uq_sys_permission_doctype_role"),
)

# One row per signed-in browser. The cookie holds the token; this holds its hash.
sys_session = Table(
    "sys_session",
    system_metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user", String(140), ForeignKey("sys_user.name", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)


def install_system_tables(bind: Engine | Connection) -> list[str]:
    """Create any missing system tables and return their names. Safe to run repeatedly."""
    existing = set(inspect(bind).get_table_names())
    system_metadata.create_all(bind)
    return [name for name in system_metadata.tables if name not in existing]
