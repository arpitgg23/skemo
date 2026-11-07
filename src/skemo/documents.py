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

from .apps import site_hooks
from .auth import ADMIN, User
from .errors import DocumentConflict, DocumentNotFound, DocumentValidationError, Errors
from .fieldtypes import timestamp_adapter, value_adapter
from .hooks import Doc, DocumentHooks, run_hooks
from .meta import DocField, DocType
from .naming import MAX_NAME, make_name
from .permissions import Action, Permissions
from .query import MAX_LIMIT, ListQuery, QueryError, public_columns
from .store import load_doctypes
from .sync import build_table

DEFAULT_USER = ADMIN
# Set by the system. Clients may echo them back from a read, so they're ignored on write,
# except modified_at on update, which is compared to catch concurrent edits.
SYSTEM_COLUMNS = ("owner", "created_at", "modified_at", "modified_by")
# A child row carries which record it belongs to; a client never sets these.
CHILD_COLUMNS = ("parent", "parenttype", "parentfield", "idx")

# Re-exported so `from skemo.documents import DocumentNotFound` still works.
__all__ = [
    "DEFAULT_USER",
    "DocumentConflict",
    "DocumentNotFound",
    "DocumentValidationError",
    "Records",
]


class Records:
    """Record operations on one site, as one user.

    `doctypes` must be the site's complete set. Every method checks `permissions` first,
    so a caller can't reach a DocType the user has no rule for.
    """

    def __init__(
        self,
        conn: Connection,
        doctypes: Mapping[str, DocType],
        *,
        user: str = DEFAULT_USER,
        permissions: Permissions | None = None,
        hooks: Mapping[str, DocumentHooks] | None = None,
    ) -> None:
        self.conn = conn
        self.doctypes = doctypes
        self.user = user
        # An app's Python hooks, by DocType. Loaded once per request by Records.load.
        self.hooks = hooks or {}
        # No permissions given means the caller is trusted (the CLI, or a test).
        self.permissions = permissions or Permissions(
            user=user, roles=frozenset(), rules={}, admin=True
        )
        self._tables: dict[str, Table] = {}

    @classmethod
    def load(
        cls,
        conn: Connection,
        *,
        user: str | User = DEFAULT_USER,
        check_permissions: bool = False,
        with_hooks: bool = True,
    ) -> Records:
        name = user.name if isinstance(user, User) else user
        permissions = None
        if check_permissions:
            if not isinstance(user, User):
                raise ValueError("checking permissions needs the User, not just their name")
            permissions = Permissions.for_user(conn, user)
        doctypes = load_doctypes(conn)
        return cls(
            conn,
            doctypes,
            user=name,
            permissions=permissions,
            hooks=site_hooks(conn) if with_hooks else {},
        )

    def insert(
        self, doctype: str, data: Mapping[str, Any], *, today: date | None = None
    ) -> dict[str, Any]:
        """Create a record, with any child rows, and return it as stored."""
        dt = self._doctype(doctype, "create")
        today = today or date.today()
        payload, errors = _split(dt, data)
        given_name = payload.pop("name", None)
        grids, grid_errors = self._clean_grids(dt, payload, today)
        errors += grid_errors
        values = {f.fieldname: _default(f, today) for f in dt.data_fields} | payload
        cleaned, field_errors = _clean(dt, values)
        errors += field_errors
        errors += _name_errors(dt, given_name, cleaned, errors)
        errors += self._link_errors(dt, cleaned, cleaned.keys())
        if errors:
            raise DocumentValidationError(errors)

        before_hooks = dict(cleaned)
        doc = self._doc(dt, {**cleaned, **grids}, is_new=True)
        run_hooks(self.hooks.get(doctype), ("before_insert", "before_save", "validate"), doc)
        # Hooks may have changed values, so check those the same way as anything else.
        cleaned, errors = _clean(dt, doc.values)
        grids, grid_errors = self._clean_grids(dt, doc.values, today)
        errors += grid_errors
        errors += self._link_errors(dt, cleaned, _differs(cleaned, before_hooks))
        if errors:
            raise DocumentValidationError(errors)

        if dt.autoname == "prompt":
            name = given_name.strip()
        else:
            name = make_name(self.conn, dt, cleaned, today=today)
        row = {**cleaned, "name": name, "owner": self.user, "modified_by": self.user}
        written = self._write(dt, insert(self._table(dt)).values(row), row)
        self._write_grids(dt, name, grids)
        doc.name = name
        doc.values = {**written, **grids}
        return self._after(dt, doc, ("after_insert", "after_save"), written)

    def get(self, doctype: str, name: str) -> dict[str, Any]:
        dt = self._doctype(doctype, "read")
        return self._with_children(dt, _public(self._fetch(dt, name)))

    def link_titles(self, doctype: str | DocType, doc: Mapping[str, Any]) -> dict[str, Any]:
        """The words behind a record's Link values: `{"customer": "Asha Textiles"}`.

        A Link field stores the target's name, which is usually a code, so anything shown
        to a person needs the target's title field as well. Grids come back in the same
        shape the record has, one entry per row, so a caller can walk the two together:

            {"customer": "Asha Textiles", "items": [{"product": "Cotton fabric"}, {}]}

        One query per target DocType, however many values point at it. A link is simply
        left out — and the caller falls back to the stored name — when the target has no
        title field, when the record has gone, or when this user may not read that
        DocType. Someone limited to their own records only gets titles for records they
        own, the same answer `_fetch` gives them.
        """
        dt = doctype if isinstance(doctype, DocType) else self.doctypes[doctype]
        wanted: dict[str, set[str]] = {}
        for target, _, value in self._links(dt, doc):
            wanted.setdefault(target, set()).add(value)
        found = {target: self._titles(target, names) for target, names in wanted.items()}

        def titles_of(fields: Sequence[DocField], values: Mapping[str, Any]) -> dict[str, str]:
            return {
                f.fieldname: title
                for target, f, value in self._link_fields(fields, values)
                if (title := found.get(target, {}).get(value)) and f.fieldname
            }

        titles: dict[str, Any] = titles_of(dt.data_fields, doc)
        for field, child, rows in self._grids(dt, doc):
            titles[field.fieldname] = [titles_of(child.data_fields, row) for row in rows]
        return titles

    def _links(
        self, doctype: DocType, doc: Mapping[str, Any]
    ) -> list[tuple[str, DocField, str]]:
        """Every Link value in a record and its grids, as (target DocType, field, value)."""
        links = list(self._link_fields(doctype.data_fields, doc))
        for _, child, rows in self._grids(doctype, doc):
            for row in rows:
                links += self._link_fields(child.data_fields, row)
        return links

    @staticmethod
    def _link_fields(
        fields: Sequence[DocField], values: Mapping[str, Any]
    ) -> list[tuple[str, DocField, str]]:
        return [
            (f.options, f, value)
            for f in fields
            if f.fieldtype == "Link"
            and f.options
            and isinstance(value := values.get(f.fieldname), str)
            and value
        ]

    def _grids(
        self, doctype: DocType, doc: Mapping[str, Any]
    ) -> list[tuple[DocField, DocType, list[Mapping[str, Any]]]]:
        """Each grid on the record, with its child DocType and the rows actually there."""
        grids = []
        for f in doctype.table_fields:
            child = self.doctypes.get(f.options or "")
            rows = doc.get(f.fieldname) or []
            if child is not None and rows:
                grids.append((f, child, list(rows)))
        return grids

    def _titles(self, doctype: str, names: Collection[str]) -> dict[str, str]:
        """{record name: its title} for one DocType, in one query. Empty when not allowed."""
        target = self.doctypes.get(doctype)
        if target is None or not self.permissions.can(doctype, "read"):
            return {}
        title = target.title_field
        if title is None or title.fieldname is None:
            return {}
        table = self._table(target)
        query = select(table.c.name, table.c[title.fieldname]).where(table.c.name.in_(names))
        if self.permissions.owner_only(doctype, "read"):
            query = query.where(table.c.owner == self.user)
        return {name: text for name, text in self.conn.execute(query) if text}

    def update(self, doctype: str, name: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Change some fields of a record and return it as stored.

        If `data` includes `modified_at` from an earlier read, the update is refused when the
        record has changed since, so nobody silently overwrites someone else's edit. A save
        that changes nothing writes nothing.
        """
        dt = self._doctype(doctype, "write")
        current = self._fetch(dt, name, lock=True)
        payload, errors = _split(dt, data, keep=("modified_at",))
        stored_grids = self._read_grids(dt, name)
        grids, grid_errors = self._clean_grids(dt, payload, date.today(), current=stored_grids)
        errors += grid_errors
        if payload.pop("name", name) != name:
            errors.append({"field": "name", "message": "can't be changed yet"})
        if "modified_at" in payload:
            seen = _timestamp(payload.pop("modified_at"))
            if seen is None:
                errors.append({"field": "modified_at", "message": "isn't a valid timestamp"})
            elif seen != current["modified_at"]:
                raise DocumentConflict(
                    f"{dt.label} {name!r} was changed by someone else after you loaded it. "
                    "Reload it and try again.",
                    "stale",
                )

        values = {f.fieldname: current[f.fieldname] for f in dt.data_fields} | payload
        cleaned, field_errors = _clean(dt, values)
        errors += field_errors
        errors += self._link_errors(
            dt, cleaned, [k for k in payload if cleaned.get(k) != current[k]]
        )
        if errors:
            raise DocumentValidationError(errors)

        before_hooks = dict(cleaned)
        doc = self._doc(
            dt,
            {**cleaned, **grids},
            is_new=False,
            name=name,
            previous={**current, **stored_grids},
        )
        run_hooks(self.hooks.get(doctype), ("before_update", "before_save", "validate"), doc)
        cleaned, errors = _clean(dt, doc.values)
        grids, grid_errors = self._clean_grids(dt, doc.values, date.today(), current=stored_grids)
        errors += grid_errors
        # Whatever a hook changed counts as part of this save, and is checked like the rest.
        changed = {k: v for k, v in cleaned.items() if v != current[k]}
        errors += self._link_errors(dt, cleaned, _differs(cleaned, before_hooks))
        if errors:
            raise DocumentValidationError(errors)
        grids_changed = any(rows != stored_grids[field] for field, rows in grids.items())
        if not changed and not grids_changed:
            return self._with_children(dt, _public(current))
        if grids_changed:
            # Only a grid changed, but the record was still touched: the UPDATE below
            # still stamps modified_at and modified_by.
            self._write_grids(dt, name, grids)

        table = self._table(dt)
        stmt = (
            update_stmt(table)
            .where(table.c.id == current["id"])
            .values({**changed, "modified_at": func.now(), "modified_by": self.user})
        )
        written = self._write(dt, stmt, changed)
        doc.values = dict(written)
        return self._after(dt, doc, ("after_update", "after_save"), written)

    def delete(self, doctype: str, name: str) -> None:
        """Delete a record after archiving it. Refused while other records link to it."""
        dt = self._doctype(doctype, "delete")
        current = self._fetch(dt, name, lock=True)
        self._check_not_linked(dt, name, current["id"])
        stored = self._with_children(dt, _public(current))
        doc = self._doc(dt, stored, is_new=False, name=name, previous=stored)
        run_hooks(self.hooks.get(doctype), ("before_delete",), doc)
        table = self._table(dt)
        quoted = self.conn.dialect.identifier_preparer.quote(table.name)
        batch_id = uuid.uuid4()
        # The record's grid rows go to the archive with it, under the same batch.
        self._archive_grids(dt, name, batch_id)
        self.conn.execute(
            text(
                "INSERT INTO sys_archive (batch_id, actor, table_name, row_id, value) "
                f"SELECT :batch_id, :actor, :table, t.id, to_jsonb(t) FROM {quoted} AS t "
                "WHERE t.id = :id"
            ),
            {
                "batch_id": batch_id,
                "actor": self.user,
                "table": table.name,
                "id": current["id"],
            },
        )
        self.conn.execute(delete(table).where(table.c.id == current["id"]))
        run_hooks(self.hooks.get(doctype), ("after_delete",), doc)

    def list(
        self,
        doctype: str,
        *,
        fields: Sequence[str] | None = None,
        filters: Any = None,
        or_filters: Any = None,
        order_by: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """A page of records, plus how many match in total. See query.py for the syntax."""
        dt = self._doctype(doctype, "read")
        if not 1 <= limit <= MAX_LIMIT:
            raise QueryError(f"limit must be between 1 and {MAX_LIMIT}")
        if offset < 0:
            raise QueryError("offset can't be negative")
        table = self._table(dt)
        query = ListQuery(dt, table)
        conditions = query.conditions(filters) + query.any_of(or_filters)
        if self.permissions.owner_only(doctype, "read"):
            conditions.append(table.c.owner == self.user)
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

    # --- child rows -------------------------------------------------------------------

    def _child(self, field: DocField) -> DocType:
        child = self.doctypes.get(field.options or "")
        if child is None:  # the DocType was saved before its child; migrate puts it right
            raise DocumentNotFound(f"{field.fieldname}: no child DocType named {field.options!r}")
        return child

    def _read_grids(self, doctype: DocType, name: str) -> dict[str, list[dict[str, Any]]]:
        """Every grid's rows for one record, each in its stored order."""
        grids = {}
        for field in doctype.table_fields:
            child = self._child(field)
            table = self._table(child)
            rows = self.conn.execute(
                select(table)
                .where(
                    table.c.parenttype == doctype.name,
                    table.c.parent == name,
                    table.c.parentfield == field.fieldname,
                )
                .order_by(table.c.idx)
            ).mappings()
            grids[field.fieldname] = [
                {f.fieldname: row[f.fieldname] for f in child.data_fields} for row in rows
            ]
        return grids

    def _with_children(self, doctype: DocType, record: dict[str, Any]) -> dict[str, Any]:
        if not doctype.table_fields:
            return record
        return {**record, **self._read_grids(doctype, record["name"])}

    def _clean_grids(
        self,
        doctype: DocType,
        payload: dict[str, Any],
        today: date,
        current: Mapping[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], Errors]:
        """Take the grids out of the payload and check every row, like any other value.

        A grid left out of the payload keeps the rows it already had.
        """
        grids: dict[str, list[dict[str, Any]]] = {}
        errors: Errors = []
        for field in doctype.table_fields:
            fieldname = field.fieldname or ""
            if fieldname not in payload:
                grids[fieldname] = list(current[fieldname]) if current else []
                continue
            given = payload.pop(fieldname)
            if given is None:
                grids[fieldname] = []
                continue
            if not isinstance(given, Sequence) or isinstance(given, str):
                errors.append({"field": fieldname, "message": "should be a list of rows"})
                grids[fieldname] = list(current[fieldname]) if current else []
                continue
            child = self._child(field)
            rows: list[dict[str, Any]] = []
            for index, raw in enumerate(given, start=1):
                if not isinstance(raw, Mapping):
                    errors.append({"field": fieldname, "message": f"row {index} isn't a row"})
                    continue
                row_payload, row_errors = _split(child, raw, ignore=CHILD_COLUMNS)
                row_payload.pop("name", None)  # child rows have no name of their own
                values = {f.fieldname: _default(f, today) for f in child.data_fields}
                cleaned, field_errors = _clean(child, values | row_payload)
                errors += [
                    {"field": fieldname, "message": f"row {index}: {e['field']} {e['message']}"}
                    for e in row_errors + field_errors
                ]
                errors += [
                    {"field": fieldname, "message": f"row {index}: {e['message']}"}
                    for e in self._link_errors(child, cleaned, cleaned.keys())
                ]
                rows.append(cleaned)
            grids[fieldname] = rows
        return grids, errors

    def _write_grids(
        self, doctype: DocType, name: str, grids: Mapping[str, list[dict[str, Any]]]
    ) -> None:
        """Replace a record's grid rows with the ones given, in the order given."""
        for field in doctype.table_fields:
            fieldname = field.fieldname or ""
            table = self._table(self._child(field))
            belongs_to = (
                table.c.parenttype == doctype.name,
                table.c.parent == name,
                table.c.parentfield == fieldname,
            )
            self.conn.execute(delete(table).where(*belongs_to))
            rows = grids.get(fieldname) or []
            if rows:
                self.conn.execute(
                    insert(table),
                    [
                        {
                            **row,
                            "parent": name,
                            "parenttype": doctype.name,
                            "parentfield": fieldname,
                            "idx": index,
                        }
                        for index, row in enumerate(rows, start=1)
                    ],
                )

    def _archive_grids(self, doctype: DocType, name: str, batch_id: uuid.UUID) -> None:
        """Copy a record's grid rows to the archive, then remove them."""
        quote = self.conn.dialect.identifier_preparer.quote
        for field in doctype.table_fields:
            table = self._table(self._child(field))
            params = {
                "batch_id": batch_id,
                "actor": self.user,
                "table": table.name,
                "parenttype": doctype.name,
                "parent": name,
                "parentfield": field.fieldname,
            }
            where = (
                "t.parenttype = :parenttype AND t.parent = :parent AND t.parentfield = :parentfield"
            )
            self.conn.execute(
                text(
                    "INSERT INTO sys_archive (batch_id, actor, table_name, row_id, value) "
                    f"SELECT :batch_id, :actor, :table, t.id, to_jsonb(t) FROM {quote(table.name)} "
                    f"AS t WHERE {where}"
                ),
                params,
            )
            self.conn.execute(
                text(f"DELETE FROM {quote(table.name)} AS t WHERE {where}"),
                {k: v for k, v in params.items() if k in ("parenttype", "parent", "parentfield")},
            )

    def _after(
        self, doctype: DocType, doc: Doc, names: tuple[str, ...], written: dict[str, Any]
    ) -> dict[str, Any]:
        """Run the after-save hooks, then return the record as it now stands.

        A hook may write to the record itself, so the row is read back rather than
        returning what the save alone produced.
        """
        hooks = self.hooks.get(doctype.name)
        if hooks is None:
            return self._with_children(doctype, written)
        run_hooks(hooks, names, doc)
        return self._with_children(doctype, _public(self._fetch(doctype, str(doc.name))))

    def _doc(
        self,
        doctype: DocType,
        values: dict[str, Any],
        *,
        is_new: bool,
        name: str | None = None,
        previous: Mapping[str, Any] | None = None,
    ) -> Doc:
        return Doc(
            doctype,
            values,
            is_new=is_new,
            user=self.user,
            conn=self.conn,
            name=name,
            previous=previous,
        )

    def _doctype(self, name: str, action: Action | None = None) -> DocType:
        doctype = self.doctypes.get(name)
        if doctype is None:
            raise DocumentNotFound(f"no DocType named {name!r}")
        if doctype.is_child and action is not None:
            # Child rows are reached through the record that owns them, so that their
            # permissions are the parent's and they can't be edited behind its back.
            owners = sorted(dt.label for dt in self.doctypes.values() if name in dt.children())
            inside = f" It's a table inside {' and '.join(owners)}." if owners else ""
            raise DocumentNotFound(
                f"{doctype.label} isn't a DocType you can use on its own.{inside}"
            )
        if action is not None:
            self.permissions.require(name, action, doctype.label)
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
        # Someone limited to their own records is told it isn't there, not that it is
        # someone else's: the answer is the same whether or not the record exists.
        rule = self.permissions.rule(doctype.name)
        if rule is not None and rule.if_owner and row["owner"] != self.user:
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
            raise DocumentConflict(_duplicate_message(doctype, e, values), "duplicate") from e
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
                        f"{other.label} {linked!r} links to it",
                        "linked",
                    )


def _split(
    doctype: DocType,
    data: Mapping[str, Any],
    *,
    keep: Collection[str] = (),
    ignore: Collection[str] = (),
) -> tuple[dict[str, Any], Errors]:
    """Separate what a client may write from system columns (ignored) and unknown keys."""
    fieldnames = {f.fieldname for f in doctype.data_fields}
    fieldnames |= {f.fieldname for f in doctype.table_fields}
    payload: dict[str, Any] = {}
    errors: Errors = []
    for key, value in data.items():
        if key in fieldnames or key == "name" or key in keep:
            payload[key] = value
        elif key not in SYSTEM_COLUMNS and key not in ignore:
            errors.append({"field": key, "message": f"isn't a field of {doctype.label}"})
    return payload, errors


def _differs(values: Mapping[str, Any], other: Mapping[str, Any]) -> list[str]:
    """Fieldnames whose value a hook changed, so only those are re-checked."""
    return [name for name, value in values.items() if other.get(name) != value]


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
