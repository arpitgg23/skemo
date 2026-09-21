"""List queries: turning `fields`, `filters` and `order_by` into safe SQL.

Field names are checked against the DocType and values are always bound, never pasted in.

Filters are either a dict of equalities, {"stage": "Open"}, or a list of [field, operator, value]:

    [["amount", ">", 1000], ["stage", "in", ["Open", "Won"]], ["email", "is", "set"]]

Operators: = != < <= > >= like, not like, in, not in, is. `like` ignores case and uses % as the
wildcard; `is` takes "set" or "not set". Values are converted to the field's type first, so
dates can be given as "2026-10-01".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Column, Table, Text, cast
from sqlalchemy.sql.elements import ColumnElement

from .fieldtypes import timestamp_adapter, value_adapter
from .meta import DocType

MAX_LIMIT = 1000
_TEXT = TypeAdapter(str)
SYSTEM_ADAPTERS = {
    "name": _TEXT,
    "owner": _TEXT,
    "created_at": timestamp_adapter,
    "modified_at": timestamp_adapter,
    "modified_by": _TEXT,
}
_COMPARE: dict[str, Callable[[Any, Any], ColumnElement[bool]]] = {
    "=": lambda column, value: column == value,
    "!=": lambda column, value: column != value,
    "<": lambda column, value: column < value,
    "<=": lambda column, value: column <= value,
    ">": lambda column, value: column > value,
    ">=": lambda column, value: column >= value,
}
OPERATORS = (*_COMPARE, "like", "not like", "in", "not in", "is")


class QueryError(ValueError):
    """A list request asked for something invalid."""


def public_columns(doctype: DocType, table: Table) -> list[Column[Any]]:
    """The columns a record shows: its name, its fields, then who changed it and when."""
    names = [
        "name",
        *(f.fieldname for f in doctype.data_fields),
        "owner",
        "created_at",
        "modified_at",
        "modified_by",
    ]
    return [table.c[n] for n in names]


def _is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str)


class ListQuery:
    def __init__(self, doctype: DocType, table: Table) -> None:
        self.doctype = doctype
        self.table = table
        self.adapters: dict[str, TypeAdapter[Any]] = {
            **SYSTEM_ADAPTERS,
            **{f.fieldname: value_adapter(f) for f in doctype.data_fields},
        }

    def columns(self, fields: Sequence[str] | None) -> list[Column[Any]]:
        """None means name plus the fields marked in_list_view; ["*"] means everything."""
        if fields is not None and list(fields) == ["*"]:
            return public_columns(self.doctype, self.table)
        if fields is None:
            fields = [f.fieldname for f in self.doctype.data_fields if f.in_list_view]
        names = list(dict.fromkeys(["name", *fields]))  # name first, no repeats
        for name in names:
            self._check_field(name)
        return [self.table.c[name] for name in names]

    def conditions(self, filters: Any) -> list[ColumnElement[bool]]:
        if filters is None:
            return []
        if isinstance(filters, Mapping):
            return [self._condition(field, "=", value) for field, value in filters.items()]
        if not _is_list(filters):
            raise QueryError("filters must be an object or a list of [field, operator, value]")
        conditions = []
        for item in filters:
            if not (_is_list(item) and len(item) == 3):
                raise QueryError(f"each filter must be [field, operator, value], not {item!r}")
            conditions.append(self._condition(*item))
        return conditions

    def ordering(self, order_by: str | None) -> list[ColumnElement[Any]]:
        """E.g. "amount desc, title". Defaults to the most recently modified first."""
        if not order_by:
            return [self.table.c.modified_at.desc()]
        clauses = []
        for part in order_by.split(","):
            words = part.split()
            direction = words[1].lower() if len(words) == 2 else "asc"
            if not 1 <= len(words) <= 2 or direction not in ("asc", "desc"):
                raise QueryError(
                    f"order_by {part.strip()!r} should look like 'field' or 'field desc'"
                )
            self._check_field(words[0])
            column = self.table.c[words[0]]
            clauses.append(column.desc() if direction == "desc" else column.asc())
        return clauses

    def _condition(self, field: Any, operator: Any, value: Any) -> ColumnElement[bool]:
        self._check_field(field)
        column = self.table.c[field]
        op = str(operator).strip().lower()
        if op == "is":
            if str(value).lower() == "set":
                return column.is_not(None)
            if str(value).lower() == "not set":
                return column.is_(None)
            raise QueryError(f'filter on {field}: \'is\' takes "set" or "not set"')
        if op in ("like", "not like"):
            if not isinstance(value, str):
                raise QueryError(f'filter on {field}: {op!r} takes text, like "%acme%"')
            as_text = cast(column, Text)
            return as_text.ilike(value) if op == "like" else as_text.not_ilike(value)
        if op in ("in", "not in"):
            if not _is_list(value):
                raise QueryError(f"filter on {field}: {op!r} takes a list of values")
            values = [self._coerce(field, v) for v in value]
            return column.in_(values) if op == "in" else column.not_in(values)
        if op not in _COMPARE:
            raise QueryError(f"unknown operator {operator!r}; use one of: {', '.join(OPERATORS)}")
        if value is None:
            if op in ("=", "!="):
                return column.is_(None) if op == "=" else column.is_not(None)
            raise QueryError(f"filter on {field}: {op!r} needs a value")
        return _COMPARE[op](column, self._coerce(field, value))

    def _check_field(self, name: Any) -> None:
        if name not in self.adapters:
            raise QueryError(f"{self.doctype.label} has no field {name!r}")

    def _coerce(self, field: str, value: Any) -> Any:
        try:
            return self.adapters[field].validate_python(value)
        except ValidationError as e:
            raise QueryError(f"filter on {field}: {e.errors()[0]['msg']}") from None
