"""Save and delete DocTypes.

Every call validates the change, stores the metadata and syncs the tables in a single
transaction, so either all of it happens or none of it does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from .apps import App, load_app, load_installed, record_app
from .meta import DocType
from .permissions import install_roles
from .store import delete_definition, load_doctypes, save_definition
from .sync import Renames, SyncResult, sync_schema
from .system_tables import install_system_tables

DEFAULT_ACTOR = "Administrator"


class DocTypeError(ValueError):
    """The requested change is invalid. Nothing was changed."""


def save_doctypes(
    engine: Engine,
    doctypes: Iterable[DocType],
    *,
    renames: Renames | None = None,
    actor: str = DEFAULT_ACTOR,
    app: str | None = None,
) -> SyncResult:
    """Create or update DocTypes, then sync the tables.

    `renames` maps a DocType name to {old fieldname: new fieldname} for fields renamed in
    this save, so their columns are renamed instead of dropped and re-added.
    """
    batch = list(doctypes)
    names = [dt.name for dt in batch]
    if len(names) != len(set(names)):
        raise DocTypeError("the same DocType appears twice in one save")
    renames = {name: dict(mapping) for name, mapping in (renames or {}).items() if mapping}

    with engine.begin() as conn:
        current = load_doctypes(conn)
        proposed = {**current, **{dt.name: dt for dt in batch}}
        for dt in batch:
            _check_links(dt, proposed)
        for name, mapping in renames.items():
            if name not in names:
                raise DocTypeError(f"renames given for {name!r}, which isn't being saved")
            _check_renames(current.get(name), proposed[name], mapping)
        for dt in batch:
            save_definition(conn, dt, app=app)
        return sync_schema(conn, proposed.values(), renames=renames, actor=actor)


def save_doctype(
    engine: Engine,
    doctype: DocType,
    *,
    renames: Mapping[str, str] | None = None,
    actor: str = DEFAULT_ACTOR,
) -> SyncResult:
    """Create or update one DocType. `renames` is {old fieldname: new fieldname}."""
    return save_doctypes(engine, [doctype], renames={doctype.name: renames or {}}, actor=actor)


def delete_doctype(engine: Engine, name: str, *, actor: str = DEFAULT_ACTOR) -> SyncResult:
    """Delete a DocType and drop its table. Its rows are archived first."""
    with engine.begin() as conn:
        current = load_doctypes(conn)
        if name not in current:
            raise DocTypeError(f"DocType {name!r} doesn't exist")
        linked_from = sorted(n for n, dt in current.items() if n != name and name in dt.links())
        if linked_from:
            raise DocTypeError(f"can't delete {name!r}: {', '.join(linked_from)} link to it")
        delete_definition(conn, name)
        del current[name]
        return sync_schema(conn, current.values(), actor=actor)


@dataclass
class MigrationResult:
    installed: list[str]  # system tables and columns added
    apps: list[str]  # installed apps whose DocTypes were re-read
    sync: SyncResult


def sync_site(engine: Engine, *, actor: str = DEFAULT_ACTOR) -> MigrationResult:
    """Bring a site up to date: system tables, the installed apps' DocTypes, then the tables.

    Re-reading the apps is what makes a DocType's JSON the source of truth: edit the file,
    commit it, and `migrate` applies it to every site that has the app.
    """
    with engine.begin() as conn:
        installed = install_system_tables(conn)
        install_roles(conn)  # a site made before roles existed gets them here
        apps = load_installed(conn)
        current = load_doctypes(conn)
        for app in apps:
            for doctype in app.doctypes():
                current[doctype.name] = doctype
                save_definition(conn, doctype, app=app.name)
        for dt in current.values():
            _check_links(dt, current)
        return MigrationResult(
            installed=installed,
            apps=[app.name for app in apps],
            sync=sync_schema(conn, current.values(), actor=actor),
        )


def install_app(
    engine: Engine, path: str | Path, *, actor: str = DEFAULT_ACTOR
) -> tuple[App, MigrationResult]:
    """Add an app to a site and apply its DocTypes."""
    app = load_app(path)
    with engine.begin() as conn:
        install_system_tables(conn)
        record_app(conn, app)
    return app, sync_site(engine, actor=actor)


def _check_links(doctype: DocType, known: Mapping[str, DocType]) -> None:
    for f in doctype.fields:
        if f.fieldtype == "Link" and f.options not in known:
            raise DocTypeError(
                f"{doctype.name}.{f.fieldname} links to unknown DocType {f.options!r}"
            )


def _check_renames(old: DocType | None, new: DocType, mapping: Mapping[str, str]) -> None:
    if old is None:
        raise DocTypeError(f"{new.name!r} is new, so it has no fields to rename")
    before = {f.fieldname for f in old.data_fields}
    after = {f.fieldname for f in new.data_fields}
    for src, dst in mapping.items():
        if src not in before:
            raise DocTypeError(f"{new.name}: can't rename {src!r}, it isn't a field")
        if src in after:
            raise DocTypeError(f"{new.name}: {src!r} is renamed but still in the definition")
        if dst not in after:
            raise DocTypeError(f"{new.name}: rename target {dst!r} is missing from the definition")
        if dst in before:
            raise DocTypeError(f"{new.name}: can't rename {src!r} to {dst!r}, that field exists")
