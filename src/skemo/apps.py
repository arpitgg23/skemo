"""Apps: DocTypes as files, so they live in git and ship to every site.

An app is a folder:

    crm/
      app.json            {"name": "crm", "title": "CRM", "version": "0.1.0"}
      doctypes/
        lead.json         the DocType, exactly as `skemo save-doctype` takes it
        lead.py           optional Python hooks (see hooks.py)

`skemo install-app ./crm` records it on a site; `skemo migrate` re-reads every
installed app and syncs the tables. A DocType from an app is "standard": the file is the
truth, and the builder won't let it be edited in the browser unless developer mode is on —
in which case saving writes the JSON back to the file for you to commit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError as PydanticError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .hooks import DocumentHooks, load_hooks
from .meta import DocType
from .store import app_owners
from .system_tables import sys_app

APP_FILE = "app.json"
DOCTYPES_DIR = "doctypes"
PRINT_FORMATS_DIR = "print_formats"
APP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class AppError(Exception):
    """The app folder is missing, misnamed or has a file that isn't a valid DocType."""


@dataclass(frozen=True)
class App:
    name: str
    title: str
    version: str
    path: Path

    @property
    def doctypes_dir(self) -> Path:
        return self.path / DOCTYPES_DIR

    def doctype_files(self) -> list[Path]:
        return sorted(self.doctypes_dir.glob("*.json")) if self.doctypes_dir.is_dir() else []

    def doctypes(self) -> list[DocType]:
        """Every DocType the app ships, in a stable order."""
        loaded = []
        for path in self.doctype_files():
            try:
                doctype = DocType.model_validate_json(path.read_text(encoding="utf-8"))
            except PydanticError as e:
                problem = e.errors()[0]
                where = ".".join(str(part) for part in problem["loc"]) or "(file)"
                raise AppError(f"{path}: {where}: {problem['msg']}") from None
            except (OSError, ValueError) as e:
                raise AppError(f"{path}: {e}") from None
            if doctype.name != path.stem:
                raise AppError(
                    f"{path}: the file is named {path.stem!r} but the DocType is {doctype.name!r}"
                )
            loaded.append(doctype)
        return loaded

    def hooks_for(self, doctype: str) -> DocumentHooks | None:
        return load_hooks(self.doctypes_dir / f"{doctype}.py", doctype)

    def print_format(self, doctype: str) -> Path | None:
        """The app's own printable layout for a DocType, if it ships one."""
        path = self.path / PRINT_FORMATS_DIR / f"{doctype}.html"
        return path if path.is_file() else None

    def write_doctype(self, doctype: DocType) -> Path:
        """Write a DocType back to its file, for a developer to review and commit."""
        path = self.doctypes_dir / f"{doctype.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        definition = doctype.model_dump(mode="json", exclude_defaults=True)
        path.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
        return path


def load_app(path: str | Path) -> App:
    """Read an app folder. Raises AppError if it isn't one."""
    folder = Path(path).expanduser().resolve()
    config = folder / APP_FILE
    if not config.is_file():
        raise AppError(f"no {APP_FILE} in {folder} — is that an app folder?")
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise AppError(f"{config}: {e}") from None
    name = str(data.get("name", "")).strip()
    if not APP_NAME.match(name):
        raise AppError(
            f"{config}: the app's name must be lowercase letters, digits and _, "
            f"starting with a letter (got {name!r})"
        )
    return App(
        name=name,
        title=str(data.get("title") or name.replace("_", " ").title()),
        version=str(data.get("version") or "0.0.0"),
        path=folder,
    )


def record_app(conn: Connection, app: App) -> None:
    """Remember that this site uses this app, or update where it lives."""
    stmt = insert(sys_app).values(
        name=app.name, title=app.title, version=app.version, path=str(app.path)
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[sys_app.c.name],
            set_={
                "title": stmt.excluded.title,
                "version": stmt.excluded.version,
                "path": stmt.excluded.path,
            },
        )
    )


@dataclass(frozen=True)
class InstalledApp:
    name: str
    path: Path
    version: str
    installed_at: datetime

    @property
    def missing(self) -> bool:
        return not (self.path / APP_FILE).is_file()


def installed_apps(conn: Connection) -> list[InstalledApp]:
    rows = conn.execute(select(sys_app).order_by(sys_app.c.name)).all()
    return [
        InstalledApp(name=r.name, path=Path(r.path), version=r.version, installed_at=r.installed_at)
        for r in rows
    ]


def load_installed(conn: Connection) -> list[App]:
    """Load every installed app from disk, skipping none: a missing folder is an error."""
    apps = []
    for installed in installed_apps(conn):
        if installed.missing:
            raise AppError(
                f"app {installed.name!r} isn't at {installed.path} any more. Move it back, "
                f"or point the site at its new place: skemo install-app <path>"
            )
        apps.append(load_app(installed.path))
    return apps


def site_hooks(conn: Connection) -> dict[str, DocumentHooks]:
    """The Python hooks for this site's app-owned DocTypes, by DocType name."""
    owners = app_owners(conn)
    if not owners:
        return {}
    apps = {app.name: app for app in load_installed(conn)}
    found = {}
    for doctype, app_name in owners.items():
        app = apps.get(app_name)
        hooks = app.hooks_for(doctype) if app else None
        if hooks is not None:
            found[doctype] = hooks
    return found


def print_template(conn: Connection, doctype: str) -> tuple[str, str] | None:
    """An installed app's print format for a DocType, as (template, where it came from)."""
    for app in load_installed(conn):
        path = app.print_format(doctype)
        if path is not None:
            return path.read_text(encoding="utf-8"), f"{app.name}/{PRINT_FORMATS_DIR}/{path.name}"
    return None


def app_of(conn: Connection, doctype: str, apps: list[App] | None = None) -> App | None:
    """The installed app a DocType comes from, or None if the builder made it."""
    for app in apps if apps is not None else load_installed(conn):
        if (app.doctypes_dir / f"{doctype}.json").is_file():
            return app
    return None
