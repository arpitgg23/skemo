"""Field types a DocType can use: the Postgres column each becomes, and how values are checked."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import AfterValidator, Field, StringConstraints, TypeAdapter
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Double, Numeric, String, Text
from sqlalchemy.types import TypeEngine

if TYPE_CHECKING:
    from .meta import DocField

DEFAULT_LENGTH = 140
BIGINT_RANGE = Field(ge=-(2**63), le=2**63 - 1)


def assume_utc(value: datetime) -> datetime:
    # Until sites get a timezone setting, a datetime without an offset is taken as UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


Timestamp = Annotated[datetime, AfterValidator(assume_utc)]
timestamp_adapter: TypeAdapter[datetime] = TypeAdapter(Timestamp)


def _string(max_length: int) -> Any:
    return Annotated[str, StringConstraints(max_length=max_length)]


@dataclass(frozen=True)
class FieldType:
    name: str
    # The column type, and the Python type values are validated as (a pydantic annotation).
    # Both are None for layout-only types, which have no column and hold no value.
    column_type: Callable[[DocField], TypeEngine] | None = None
    value_type: Callable[[DocField], Any] | None = None
    # Select needs its choices; Link needs the DocType it points to.
    needs_options: bool = False

    @property
    def has_column(self) -> bool:
        return self.column_type is not None


FIELD_TYPES: dict[str, FieldType] = {
    ft.name: ft
    for ft in (
        FieldType(
            "Data",
            lambda f: String(f.length or DEFAULT_LENGTH),
            lambda f: _string(f.length or DEFAULT_LENGTH),
        ),
        FieldType("Text", lambda f: Text(), lambda f: str),
        FieldType("Int", lambda f: BigInteger(), lambda f: Annotated[int, BIGINT_RANGE]),
        FieldType(
            "Float",
            lambda f: Double(),
            lambda f: Annotated[float, Field(allow_inf_nan=False)],
        ),
        FieldType(
            "Currency",
            lambda f: Numeric(18, 2),
            lambda f: Annotated[Decimal, Field(max_digits=18, decimal_places=2)],
        ),
        FieldType("Date", lambda f: Date(), lambda f: date),
        FieldType("Datetime", lambda f: DateTime(timezone=True), lambda f: Timestamp),
        FieldType("Check", lambda f: Boolean(), lambda f: bool),
        FieldType(
            "Select",
            lambda f: String(DEFAULT_LENGTH),
            lambda f: Literal[f.choices],
            needs_options=True,
        ),
        # Stores the `name` of the linked record, like Frappe.
        FieldType(
            "Link",
            lambda f: String(DEFAULT_LENGTH),
            lambda f: _string(DEFAULT_LENGTH),
            needs_options=True,
        ),
        FieldType("Section Break"),
        FieldType("Column Break"),
    )
}

_adapters: dict[tuple[str, int | None, str | None], TypeAdapter[Any]] = {}


def value_adapter(f: DocField) -> TypeAdapter[Any]:
    """The validator for one field's values, cached by the properties that shape its type."""
    key = (f.fieldtype, f.length, f.options)
    adapter = _adapters.get(key)
    if adapter is None:
        adapter = _adapters[key] = TypeAdapter(FIELD_TYPES[f.fieldtype].value_type(f))
    return adapter
