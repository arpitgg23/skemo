"""Records: create, read, update, delete and list the rows of any DocType.

Values are checked against the DocType's fields before anything is written, and every problem
is reported at once, per field. Each method runs in the caller's transaction, so a failed
call leaves nothing behind, not even a used-up series number.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Insert, MetaData, Table, Update, delete, func, insert, select, text
from sqlalchemy import update as update_stmt
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.exc import IntegrityError

from .fieldtypes import timestamp_adapter, value_adapter
from .meta import DocField, DocType
from .naming import MAX_NAME, make_name
from .query import MAX_LIMIT, ListQuery, QueryError, public_columns
from .store import load_doctypes
from .sync import build_table

DEFAULT_USER = "Administrator"  # until logins arrive in M5
# Set by the system. Clients may echo them back from a read, so they're ignored on write,
# except modified_at on update, which is compared to catch concurrent edits.
SYSTEM_COLUMNS = ("owner", "created_at", "modified_at", "modified_by")

Errors = list[dict[str, str]]


class DocumentNotFound(LookupError):
    pass


class DocumentConflict(Exception):
    """The write clashes with existing data: a duplicate, a link, or someone else's edit."""


class DocumentValidationError(ValueError):
    """Values that don't fit the DocType. `errors` lists every problem as {field, message}."""

    def __init__(self, errors: Errors) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))


class Records:
    """Record operations on one site. `doctypes` must be the site's complete set."""

    def __init__(
        self, conn: Connection, doctypes: Mapping[str, DocType], *, user: str = DEFAULT_USER
    ) -> None:
        self.conn = conn
        self.doctypes = doctypes
        self.user = user
        self._tables: dict[str, Table] = {}

    @classmethod
    def load(cls, conn: Connection, *, user: str = DEFAULT_USER) -> Records:
        return cls(conn, load_doctypes(conn), user=user)

    def insert(
        self, doctype: str, data: Mapping[str, Any], *, today: date | None = None
    ) -> dict[str, Any]:
        """Create a record and return it as stored."""
        dt = self._doctype(doctype)
        today = today or date.today()
        payload, errors = _split(dt, data)
        given_name = payload.pop("name", None)
        values = {f.fieldname: _default(f, today) for f in dt.data_fields} | payload
        cleaned, field_errors = _clean(dt, values)
        errors += field_errors
        errors += _name_errors(dt, given_name, cleaned, errors)
        errors += self._link_errors(dt, cleaned, cleaned.keys())
        if errors:
            raise DocumentValidationError(errors)

        if dt.autoname == "prompt":
            name = given_name.strip()
        else:
            name = make_name(self.conn, dt, cleaned, today=today)
        row = {**cleaned, "name": name, "owner": self.user, "modified_by": self.user}
        return self._write(dt, insert(self._table(dt)).values(row), row)

    def get(self, doctype: str, name: str) -> dict[str, Any]:
        return _public(self._fetch(self._doctype(doctype), name))

    def update(self, doctype: str, name: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Change some fields of a record and return it as stored.

        If `data` includes `modified_at` from an earlier read, the update is refused when the
        record has changed since, so nobody silently overwrites someone else's edit. A save
        that changes nothing writes nothing.
        """
        dt = self._doctype(doctype)
        current = self._fetch(dt, name, lock=True)
        payload, errors = _split(dt, data, keep=("modified_at",))
        if payload.pop("name", name) != name:
            errors.append({"field": "name", "message": "can't be changed yet"})
        if "modified_at" in payload:
            seen = _timestamp(payload.pop("modified_at"))
            if seen is None:
                errors.append({"field": "modified_at", "message": "isn't a valid timestamp"})
            elif seen != current["modified_at"]:
                raise DocumentConflict(
                    f"{dt.label} {name!r} was changed by someone else after you loaded it. "
                    "Reload it and try again."
                )

        values = {f.fieldname: current[f.fieldname] for f in dt.data_fields} | payload
        cleaned, field_errors = _clean(dt, values)
        errors += field_errors
        changed = {k: cleaned[k] for k in payload if k in cleaned and cleaned[k] != current[k]}
        errors += self._link_errors(dt, cleaned, changed.keys())
        if errors:
            raise DocumentValidationError(errors)
        if not changed:
            return _public(current)

        table = self._table(dt)
        stmt = (
            update_stmt(table)
            .where(table.c.id == current["id"])
            .values({**changed, "modified_at": func.now(), "modified_by": self.user})
        )
        return self._write(dt, stmt, changed)

    def delete(self, doctype: str, name: str) -> None:
        """Delete a record after archiving it. Refused while other records link to it."""
        dt = self._doctype(doctype)
        current = self._fetch(dt, name, lock=True)
        self._check_not_linked(dt, name, current["id"])
        table = self._table(dt)
        quoted = self.conn.dialect.identifier_preparer.quote(table.name)
        self.conn.execute(
            text(
                "INSERT INTO sys_archive (batch_id, actor, table_name, row_id, value) "
                f"SELECT :batch_id, :actor, :table, t.id, to_jsonb(t) FROM {quoted} AS t "
                "WHERE t.id = :id"
            ),
            {
                "batch_id": uuid.uuid4(),
                "actor": self.user,
                "table": table.name,
                "id": current["id"],
            },
        )
        self.conn.execute(delete(table).where(table.c.id == current["id"]))

    def list(
        self,
        doctype: str,
        *,
        fields: Sequence[str] | None = None,
        filters: Any = None,
        order_by: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """A page of records, plus how many match in total. See query.py for the syntax."""
        dt = self._doctype(doctype)
        if not 1 <= limit <= MAX_LIMIT:
            raise QueryError(f"limit must be between 1 and {MAX_LIMIT}")
        if offset < 0:
            raise QueryError("offset can't be negative")
        table = self._table(dt)
        query = ListQuery(dt, table)
        conditions = query.conditions(filters)
        page = self.conn.execute(
            select(*query.columns(fields))
            .where(*conditions)
            .order_by(*query.ordering(order_by), table.c.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings()
        data = [dict(row) for row in page]
        total = self.conn.execute(
            select(func.count()).select_from(table).where(*conditions)
        ).scalar_one()
        return {"data": data, "total": total}

    def _doctype(self, name: str) -> DocType:
        doctype = self.doctypes.get(name)
        if doctype is None:
            raise DocumentNotFound(f"no DocType named {name!r}")
        return doctype

    def _table(self, doctype: DocType) -> Table:
        table = self._tables.get(doctype.name)
        if table is None:
            table = self._tables[doctype.name] = build_table(doctype, MetaData())
        return table

    def _fetch(self, doctype: DocType, name: str, *, lock: bool = False) -> RowMapping:
        table = self._table(doctype)
        query = select(table.c.id, *public_columns(doctype, table)).where(table.c.name == name)
        if lock:
            query = query.with_for_update()
        row = self.conn.execute(query).mappings().first()
        if row is None:
            raise DocumentNotFound(f"{doctype.label} {name!r} not found")
        return row

    def _write(
        self, doctype: DocType, stmt: Insert | Update, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            result = self.conn.execute(
                stmt.returning(*public_columns(doctype, self._table(doctype)))
            )
        except IntegrityError as e:
            raise DocumentConflict(_duplicate_message(doctype, e, values)) from e
        return dict(result.mappings().one())

    def _link_errors(
        self, doctype: DocType, values: Mapping[str, Any], fieldnames: Collection[str]
    ) -> Errors:
        errors = []
        for f in doctype.data_fields:
            value = values.get(f.fieldname)
            if f.fieldtype != "Link" or f.fieldname not in fieldnames or value is None:
                continue
            target_doctype = self.doctypes[f.options]
            target = self._table(target_doctype)
            if self.conn.execute(select(target.c.id).where(target.c.name == value)).first() is None:
                errors.append(
                    {
                        "field": f.fieldname,
                        "message": f"{target_doctype.label} {value!r} doesn't exist",
                    }
                )
        return errors

    def _check_not_linked(self, doctype: DocType, name: str, row_id: int) -> None:
        for other in self.doctypes.values():
            for f in other.data_fields:
                if f.fieldtype != "Link" or f.options != doctype.name:
                    continue
                source = self._table(other)
                query = select(source.c.name).where(source.c[f.fieldname] == name)
                if other.name == doctype.name:
                    query = query.where(source.c.id != row_id)  # linking to itself is fine
                linked = self.conn.execute(query.limit(1)).scalar()
                if linked is not None:
                    raise DocumentConflict(
                        f"can't delete {doctype.label} {name!r}: "
                        f"{other.label} {linked!r} links to it"
                    )


def _split(
    doctype: DocType, data: Mapping[str, Any], *, keep: Collection[str] = ()
) -> tuple[dict[str, Any], Errors]:
    """Separate what a client may write from system columns (ignored) and unknown keys."""
    fieldnames = {f.fieldname for f in doctype.data_fields}
    payload: dict[str, Any] = {}
    errors: Errors = []
    for key, value in data.items():
        if key in fieldnames or key == "name" or key in keep:
            payload[key] = value
        elif key not in SYSTEM_COLUMNS:
            errors.append({"field": key, "message": f"isn't a field of {doctype.label}"})
    return payload, errors


def _default(f: DocField, today: date) -> Any:
    if f.default is None:
        return None
    if f.has_dynamic_default:
        return today if f.fieldtype == "Date" else datetime.now(UTC)
    return f.default  # a string; _clean converts it


def _clean(doctype: DocType, values: Mapping[str, Any]) -> tuple[dict[str, Any], Errors]:
    """Convert every field's value to its type, and check required fields are filled."""
    cleaned: dict[str, Any] = {}
    errors: Errors = []
    for f in doctype.data_fields:
        value = values.get(f.fieldname)
        if isinstance(value, str) and not value.strip():
            value = None  # a blank form input means "not set"
        if value is not None:
            try:
                value = value_adapter(f).validate_python(value)
            except ValidationError as e:
                errors.append({"field": f.fieldname, "message": e.errors()[0]["msg"]})
                continue
        if value is None and f.reqd:
            errors.append({"field": f.fieldname, "message": "is required"})
        cleaned[f.fieldname] = value
    return cleaned, errors


def _name_errors(
    doctype: DocType, given: Any, cleaned: Mapping[str, Any], errors: Errors
) -> Errors:
    kind, _, source = doctype.autoname.partition(":")
    if kind == "prompt":
        if not isinstance(given, str) or not given.strip():
            return [{"field": "name", "message": "is required: records here are named by hand"}]
        if len(given.strip()) > MAX_NAME:
            return [{"field": "name", "message": f"can be at most {MAX_NAME} characters"}]
        return []
    if given is not None:
        message = f"is set automatically ({doctype.autoname}), so leave it out"
        return [{"field": "name", "message": message}]
    if kind == "field" and not any(e["field"] == source for e in errors):
        value = cleaned.get(source)
        if value is None:
            return [{"field": source, "message": "is required: the record's name comes from it"}]
        if len(str(value)) > MAX_NAME:
            message = f"can be at most {MAX_NAME} characters, as the record's name comes from it"
            return [{"field": source, "message": message}]
    return []


def _timestamp(value: Any) -> datetime | None:
    try:
        return timestamp_adapter.validate_python(value)
    except ValidationError:
        return None


def _duplicate_message(doctype: DocType, error: IntegrityError, values: Mapping[str, Any]) -> str:
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None) or ""
    prefix = f"uq_{doctype.table_name}_"
    if constraint.startswith(prefix):
        column = constraint.removeprefix(prefix)
        return f"{doctype.label} with {column} {values.get(column)!r} already exists"
    return f"{doctype.label}: {str(error.orig).strip().splitlines()[0]}"


def _public(row: RowMapping) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "id"}
