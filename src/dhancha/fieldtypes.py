"""Field types a DocType can use, and the Postgres column each one becomes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Double, Numeric, String, Text
from sqlalchemy.types import TypeEngine

if TYPE_CHECKING:
    from .meta import DocField

DEFAULT_LENGTH = 140


@dataclass(frozen=True)
class FieldType:
    name: str
    # Builds the column type for a field. None means layout only: no column.
    column_type: Callable[[DocField], TypeEngine] | None = None
    # Select needs its choices; Link needs the DocType it points to.
    needs_options: bool = False

    @property
    def has_column(self) -> bool:
        return self.column_type is not None


FIELD_TYPES: dict[str, FieldType] = {
    ft.name: ft
    for ft in (
        FieldType("Data", lambda f: String(f.length or DEFAULT_LENGTH)),
        FieldType("Text", lambda f: Text()),
        FieldType("Int", lambda f: BigInteger()),
        FieldType("Float", lambda f: Double()),
        FieldType("Currency", lambda f: Numeric(18, 2)),
        FieldType("Date", lambda f: Date()),
        FieldType("Datetime", lambda f: DateTime(timezone=True)),
        FieldType("Check", lambda f: Boolean()),
        FieldType("Select", lambda f: String(DEFAULT_LENGTH), needs_options=True),
        # Stores the `name` of the linked record, like Frappe.
        FieldType("Link", lambda f: String(DEFAULT_LENGTH), needs_options=True),
        FieldType("Section Break"),
        FieldType("Column Break"),
    )
}
