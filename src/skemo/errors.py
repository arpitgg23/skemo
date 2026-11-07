"""The errors a record operation can raise.

They live here, apart from the code that raises them, so that app hooks can import them
without importing the record layer that imports the hooks.
"""

from __future__ import annotations

Errors = list[dict[str, str]]


class DocumentNotFound(LookupError):
    """No such DocType, or no such record — including one this user may not see."""


class DocumentConflict(Exception):
    """The write clashes with existing data. `code` says how: "duplicate" (a unique value is
    taken), "linked" (other records link to it) or "stale" (someone else edited it first)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class DocumentValidationError(ValueError):
    """Values that don't fit the DocType. `errors` lists every problem as {field, message}."""

    def __init__(self, errors: Errors) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
