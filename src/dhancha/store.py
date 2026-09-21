"""Reads and writes DocType definitions in sys_doctype."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .meta import DocType
from .system_tables import sys_doctype


def load_doctypes(conn: Connection) -> dict[str, DocType]:
    definitions = conn.execute(
        select(sys_doctype.c.definition).order_by(sys_doctype.c.name)
    ).scalars()
    return {dt.name: dt for dt in map(DocType.model_validate, definitions)}


def save_definition(conn: Connection, doctype: DocType) -> None:
    definition = doctype.model_dump(mode="json", exclude_defaults=True)
    stmt = insert(sys_doctype).values(
        name=doctype.name, module=doctype.module, definition=definition
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[sys_doctype.c.name],
            set_={
                "module": stmt.excluded.module,
                "definition": stmt.excluded.definition,
                "modified_at": func.now(),
            },
        )
    )


def delete_definition(conn: Connection, name: str) -> None:
    conn.execute(delete(sys_doctype).where(sys_doctype.c.name == name))
