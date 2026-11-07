"""Small helpers for building DocTypes and poking at the database in tests."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from skemo.documents import Records
from skemo.meta import DocField, DocType


class RecordsClient:
    """Calls Records methods, each in its own transaction, like one API request each."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def __getattr__(self, method: str):
        def call(*args, **kwargs):
            with self.engine.begin() as conn:
                return getattr(Records.load(conn), method)(*args, **kwargs)

        return call


def field(fieldname: str, fieldtype: str = "Data", **props) -> DocField:
    return DocField(fieldname=fieldname, fieldtype=fieldtype, **props)


def doctype(name: str, *fields: DocField, **props) -> DocType:
    return DocType(name=name, fields=list(fields), **props)


def columns(engine: Engine, table: str) -> dict[str, str]:
    """Live column name -> SQL type, straight from Postgres."""
    with engine.connect() as conn:
        return {
            c["name"]: c["type"].compile(dialect=conn.dialect)
            for c in inspect(conn).get_columns(table)
        }


def has_table(engine: Engine, table: str) -> bool:
    with engine.connect() as conn:
        return inspect(conn).has_table(table)


def run(engine: Engine, sql: str, **params) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def rows(engine: Engine, sql: str, **params) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]
