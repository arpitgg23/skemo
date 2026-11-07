"""DocType definitions: the metadata the builder edits and every engine reads."""

from __future__ import annotations

import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .fieldtypes import DEFAULT_LENGTH, FIELD_TYPES, value_adapter
from .naming import autoname_problem

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
TABLE_PREFIX = "tab_"
MAX_IDENTIFIER = 63  # Postgres silently truncates longer names
# Every DocType table gets these columns, so fields can't use the names. The last four are
# a child row's link back to the record that owns it.
STANDARD_COLUMNS = frozenset(
    {
        "id",
        "name",
        "owner",
        "created_at",
        "modified_at",
        "modified_by",
        "parent",
        "parenttype",
        "parentfield",
        "idx",
    }
)
# Defaults worked out when a record is created, instead of a fixed value.
DYNAMIC_DEFAULTS = {"Date": "today", "Datetime": "now"}


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

    @property
    def is_table(self) -> bool:
        """A grid of child rows, stored in the child DocType's own table."""
        return self.fieldtype == "Table"

    @property
    def choices(self) -> tuple[str, ...]:
        """Select choices: the non-blank lines of options."""
        return tuple(line.strip() for line in (self.options or "").splitlines() if line.strip())

    @property
    def has_dynamic_default(self) -> bool:
        default = (self.default or "").strip().lower()
        return default == DYNAMIC_DEFAULTS.get(self.fieldtype)

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
        if self.is_table:
            if name is None:
                raise ValueError("a Table field needs a fieldname")
            self.options = (self.options or "").strip()
            if not IDENTIFIER.match(self.options):
                raise ValueError(f"Table field {name!r} must name a child DocType in options")
            if self.unique or self.search_index or self.length is not None:
                raise ValueError(f"Table field {name!r} can't be unique, indexed or given a length")
            return self
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
        if self.fieldtype == "Select" and any(len(c) > DEFAULT_LENGTH for c in self.choices):
            raise ValueError(f"Select field {name!r} has a choice over {DEFAULT_LENGTH} characters")
        if self.length is not None and self.fieldtype != "Data":
            raise ValueError(f"length only applies to Data fields ({name!r} is {self.fieldtype})")
        if self.default is not None and not self.has_dynamic_default:
            try:
                value_adapter(self).validate_python(self.default)
            except ValidationError as e:
                raise ValueError(
                    f"default {self.default!r} doesn't suit {self.fieldtype} field {name!r}: "
                    f"{e.errors()[0]['msg']}"
                ) from None
        if not self.label:
            self.label = _title(name)
        return self


class DocType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str  # e.g. "sales_invoice"; its table is tab_sales_invoice
    label: str = ""
    module: str = "Core"
    description: str | None = None
    autoname: str = "hash"  # how records are named; see naming.py
    # A child DocType holds the rows of a Table field. It has no screens or names of its
    # own: its rows belong to the record that owns them.
    is_child: bool = False
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
        if self.is_child:
            # Child rows belong to their parent: they have no name of their own, and no
            # grids inside grids (that would need a second level of parents).
            if self.autoname != "hash":
                raise ValueError(f"child DocType {self.name!r} can't have a naming rule")
            for f in self.table_fields:
                raise ValueError(
                    f"child DocType {self.name!r} can't have the Table field {f.fieldname!r}"
                )
        problem = autoname_problem(self.autoname, {f.fieldname for f in self.data_fields})
        if problem:
            raise ValueError(problem)
        if not self.label:
            self.label = _title(self.name)
        return self

    @property
    def table_name(self) -> str:
        return TABLE_PREFIX + self.name

    @property
    def data_fields(self) -> list[DocField]:
        """Fields that have a column (everything except layout breaks and Tables)."""
        return [f for f in self.fields if f.has_column]

    @property
    def table_fields(self) -> list[DocField]:
        """The grids on this DocType, each holding rows of a child DocType."""
        return [f for f in self.fields if f.is_table]

    def links(self) -> set[str]:
        """Names of the DocTypes this one links to."""
        return {f.options for f in self.fields if f.fieldtype == "Link" and f.options}

    def children(self) -> set[str]:
        """Names of the child DocTypes whose rows this one holds."""
        return {f.options for f in self.table_fields if f.options}
