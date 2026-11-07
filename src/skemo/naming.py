"""Record names: every record's unique, human-readable `name`, as in Frappe.

A DocType's `autoname` decides how new records are named:

    hash                random, e.g. 3f9c01b2e7 (the default)
    prompt              whoever creates the record supplies the name
    field:<fieldname>   copied from a field, e.g. field:gstin
    format:<pattern>    e.g. format:INV-{YYYY}-{#####} gives INV-2026-00001

In a pattern, {YYYY} {YY} {MM} {DD} are today's date and {###} is a counter padded to the
number of #s. Each distinct prefix has its own counter, so a pattern with {YYYY} starts again
at 1 every year. The counter is bumped inside the record's transaction, so a failed save
gives its number back: invoice numbers stay gapless.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Collection, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .system_tables import sys_series

if TYPE_CHECKING:
    from .meta import DocType

MAX_NAME = 140
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
COUNTER = re.compile(r"^#+$")
DATE_PARTS = {"YYYY": "%Y", "YY": "%y", "MM": "%m", "DD": "%d"}


def autoname_problem(autoname: str, fieldnames: Collection[str]) -> str | None:
    """Why `autoname` doesn't work for a DocType with these fields, or None if it does."""
    if autoname in ("hash", "prompt"):
        return None
    kind, _, arg = autoname.partition(":")
    if kind == "field":
        return None if arg in fieldnames else f"autoname {autoname!r}: no field named {arg!r}"
    if kind == "format":
        placeholders = PLACEHOLDER.findall(arg)
        unknown = [p for p in placeholders if p not in DATE_PARTS and not COUNTER.match(p)]
        if unknown:
            return f"autoname {autoname!r}: unknown placeholder {{{unknown[0]}}}"
        if sum(1 for p in placeholders if COUNTER.match(p)) != 1:
            return f"autoname {autoname!r} needs exactly one counter, like {{#####}}"
        if set("{}") & set(PLACEHOLDER.sub("", arg)):
            return f"autoname {autoname!r} has an unmatched brace"
        return None
    return f"autoname {autoname!r} must be hash, prompt, field:<fieldname> or format:<pattern>"


def make_name(conn: Connection, doctype: DocType, values: Mapping[str, Any], *, today: date) -> str:
    """A new name for a record. Not for autoname "prompt", where the caller supplies it."""
    kind, _, arg = doctype.autoname.partition(":")
    if kind == "hash":
        return secrets.token_hex(5)
    if kind == "field":
        return str(values[arg])
    if kind == "format":
        return _next_in_series(conn, arg, today)
    raise ValueError(f"autoname {doctype.autoname!r} doesn't generate names")


def _next_in_series(conn: Connection, pattern: str, today: date) -> str:
    expanded = PLACEHOLDER.sub(
        lambda m: today.strftime(DATE_PARTS[m[1]]) if m[1] in DATE_PARTS else m[0], pattern
    )
    counter = PLACEHOLDER.search(expanded)  # only the counter is left
    assert counter is not None
    prefix, suffix = expanded[: counter.start()], expanded[counter.end() :]
    stmt = insert(sys_series).values(series=f"{prefix}#{suffix}", current=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[sys_series.c.series], set_={"current": sys_series.c.current + 1}
    )
    number = conn.execute(stmt.returning(sys_series.c.current)).scalar_one()
    return f"{prefix}{number:0{len(counter[1])}d}{suffix}"
