"""Python hooks: an app's own logic, run around every save and delete.

Next to a DocType's JSON, an app can put a module with a class:

    # doctypes/deal.py
    from dhancha.hooks import DocumentHooks, ValidationError

    class Deal(DocumentHooks):
        def validate(self, doc):
            if doc["amount"] and doc["amount"] < 0:
                doc.error("amount", "can't be negative")
            if doc["stage"] == "Won" and not doc["customer"]:
                raise ValidationError("A won deal needs a customer.")

        def before_save(self, doc):
            doc["probability"] = 100 if doc["stage"] == "Won" else doc["probability"]

Hooks run inside the record's transaction, so raising undoes the whole save. Values a hook
sets are checked against the field's type like any other value, so a hook can't write
something the column won't take.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from collections.abc import Iterator, Mapping, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import DocumentValidationError

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from .meta import DocType


class ValidationError(DocumentValidationError):
    """Raised by a hook to stop a save. `field` ties it to one input on the form."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__([{"field": field, "message": message}])


class Doc(MutableMapping[str, Any]):
    """One record, as a hook sees it: read and change its values like a dict."""

    def __init__(
        self,
        doctype: DocType,
        values: dict[str, Any],
        *,
        is_new: bool,
        user: str,
        conn: Connection,
        name: str | None = None,
        previous: Mapping[str, Any] | None = None,
    ) -> None:
        self.doctype = doctype
        self.values = values
        self.is_new = is_new
        self.user = user
        # The site's connection, for a hook that needs its own query. It's the same
        # transaction as the save, so anything it writes is undone if the save fails.
        self.conn = conn
        self.name = name
        # What's currently stored, on an update or delete; None when creating.
        self.previous = previous
        self.errors: list[dict[str, str]] = []

    @property
    def doctype_name(self) -> str:
        return self.doctype.name

    def __getitem__(self, fieldname: str) -> Any:
        return self.values[fieldname]

    def __setitem__(self, fieldname: str, value: Any) -> None:
        if fieldname not in self.values:
            raise KeyError(f"{self.doctype.name} has no field {fieldname!r}")
        self.values[fieldname] = value

    def __delitem__(self, fieldname: str) -> None:
        self.values[fieldname] = None

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def changed(self, fieldname: str) -> bool:
        """True when this save changes the field (always true for a new record)."""
        return self.previous is None or self.previous.get(fieldname) != self.values.get(fieldname)

    def error(self, field: str, message: str) -> None:
        """Report a problem with one field. Every problem is shown at once."""
        self.errors.append({"field": field, "message": message})

    def __repr__(self) -> str:
        return f"<Doc {self.doctype.name} {self.name or '(new)'}>"


class DocumentHooks:
    """Subclass this in an app to run logic around saves. Every method is optional."""

    def validate(self, doc: Doc) -> None:
        """Check the record before it's written, on both create and update."""

    def before_save(self, doc: Doc) -> None:
        """Change values before they're written, on both create and update."""

    def after_save(self, doc: Doc) -> None:
        """React to a record having been written, on both create and update."""

    def before_insert(self, doc: Doc) -> None: ...

    def after_insert(self, doc: Doc) -> None: ...

    def before_update(self, doc: Doc) -> None: ...

    def after_update(self, doc: Doc) -> None: ...

    def before_delete(self, doc: Doc) -> None: ...

    def after_delete(self, doc: Doc) -> None: ...


def run_hooks(hooks: DocumentHooks | None, names: tuple[str, ...], doc: Doc) -> None:
    """Call each named hook in turn, then raise if any of them reported a problem."""
    if hooks is not None:
        for name in names:
            getattr(hooks, name)(doc)
    if doc.errors:
        raise DocumentValidationError(doc.errors)


# Hook modules are read once and kept, but re-read when the file changes, so editing a
# hook during development takes effect on the next request without a restart.
_loaded: dict[Path, tuple[float, DocumentHooks | None]] = {}


def load_hooks(path: Path, doctype: str) -> DocumentHooks | None:
    """Load the DocumentHooks subclass from a module file, if the file has one."""
    if not path.is_file():
        _loaded.pop(path, None)
        return None
    modified = path.stat().st_mtime
    cached = _loaded.get(path)
    if cached is not None and cached[0] == modified:
        return cached[1]
    hooks = _import_hooks(path, doctype)
    _loaded[path] = (modified, hooks)
    return hooks


def _import_hooks(path: Path, doctype: str) -> DocumentHooks | None:
    module_name = f"dhancha_hooks.{path.parent.parent.name}.{doctype}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # so dataclasses and pickling inside it work
    spec.loader.exec_module(module)
    for value in vars(module).values():
        if (
            inspect.isclass(value)
            and issubclass(value, DocumentHooks)
            and value is not DocumentHooks
            and value.__module__ == module_name  # not one it imported
        ):
            return value()
    return None
