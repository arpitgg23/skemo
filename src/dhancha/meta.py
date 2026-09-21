"""DocType definitions: the metadata the builder edits and every engine reads."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .fieldtypes import FIELD_TYPES

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
TABLE_PREFIX = "tab_"
MAX_IDENTIFIER = 63  # Postgres silently truncates longer names
# Every DocType table gets these columns, so fields can't use the names.
STANDARD_COLUMNS = frozenset({"id", "name", "owner", "created_at", "modified_at", "modified_by"})


def _title(identifier: str) -> str:
    return identifier.replace("_", " ").title()


class DocField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fieldname: str | None = None  # optional for Section Break / Column Break
    label: str = ""
    fieldtype: str
    options: str | None = None  # Select: one choice per line. Link: the target DocType.
    length: int | None = Field(default=None, ge=1, le=1000)  # Data only; default 140
    reqd: bool = False
    unique: bool = False
    search_index: bool = False
    in_list_view: bool = False
    read_only: bool = False
    hidden: bool = False
    default: str | None = None
    description: str | None = None

    @property
    def has_column(self) -> bool:
        return FIELD_TYPES[self.fieldtype].has_column

    @model_validator(mode="after")
    def _validate(self) -> DocField:
        fieldtype = FIELD_TYPES.get(self.fieldtype)
        if fieldtype is None:
            raise ValueError(
                f"unknown fieldtype {self.fieldtype!r}; use one of: {', '.join(FIELD_TYPES)}"
            )
        name = self.fieldname
        if name is not None:
            if not IDENTIFIER.match(name) or len(name) > MAX_IDENTIFIER:
                raise ValueError(
                    f"fieldname {name!r} must start with a letter and use only lowercase "
                    f"letters, digits and _ (max {MAX_IDENTIFIER} characters)"
                )
            if name in STANDARD_COLUMNS:
                raise ValueError(f"fieldname {name!r} is reserved for a standard column")
        if not fieldtype.has_column:
            return self
        if name is None:
            raise ValueError(f"a {self.fieldtype} field needs a fieldname")
        options = (self.options or "").strip()
        if fieldtype.needs_options and not options:
            raise ValueError(f"{self.fieldtype} field {name!r} needs options")
        if self.fieldtype == "Link":
            if not IDENTIFIER.match(options):
                raise ValueError(f"Link field {name!r} must name a DocType in options")
            self.options = options
        if self.length is not None and self.fieldtype != "Data":
            raise ValueError(f"length only applies to Data fields ({name!r} is {self.fieldtype})")
        if not self.label:
            self.label = _title(name)
        return self


class DocType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str  # e.g. "sales_invoice"; its table is tab_sales_invoice
    label: str = ""
    module: str = "Core"
    description: str | None = None
    fields: list[DocField] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, name: str) -> str:
        if not IDENTIFIER.match(name) or len(TABLE_PREFIX + name) > MAX_IDENTIFIER:
            raise ValueError(
                f"DocType name {name!r} must start with a letter and use only lowercase "
                f"letters, digits and _ (max {MAX_IDENTIFIER - len(TABLE_PREFIX)} characters)"
            )
        return name

    @model_validator(mode="after")
    def _validate_fields(self) -> DocType:
        seen: set[str] = set()
        for f in self.fields:
            if f.fieldname is None:
                continue
            if f.fieldname in seen:
                raise ValueError(f"duplicate fieldname {f.fieldname!r}")
            seen.add(f.fieldname)
        if not self.label:
            self.label = _title(self.name)
        return self

    @property
    def table_name(self) -> str:
        return TABLE_PREFIX + self.name

    @property
    def data_fields(self) -> list[DocField]:
        """Fields that have a column (everything except layout breaks)."""
        return [f for f in self.fields if f.has_column]

    def links(self) -> set[str]:
        """Names of the DocTypes this one links to."""
        return {f.options for f in self.fields if f.fieldtype == "Link" and f.options}
