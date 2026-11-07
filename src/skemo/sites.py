"""Sites: each site is its own Postgres database, plus a folder under sites/ for its config.

One server can run many sites, e.g. one per client.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, make_url

from .auth import ADMIN, create_user
from .config import Settings
from .permissions import SYSTEM_MANAGER, install_roles
from .system_tables import install_system_tables

SITE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,48}[a-z0-9])?$")
CONFIG_FILE = "site_config.json"
DEFAULT_SITE_FILE = "currentsite.txt"

_engines: dict[tuple[str, str], Engine] = {}
_engines_lock = threading.Lock()


class SiteError(Exception):
    pass


@dataclass(frozen=True)
class Site:
    name: str
    db_name: str


def db_name_for(site_name: str) -> str:
    return "site_" + re.sub(r"[^a-z0-9]+", "_", site_name)


def get_engine(site: Site, settings: Settings) -> Engine:
    key = (settings.db_url, site.db_name)
    with _engines_lock:
        if key not in _engines:
            url = make_url(settings.db_url).set(database=site.db_name)
            _engines[key] = create_engine(url, pool_pre_ping=True)
        return _engines[key]


def new_site(
    name: str,
    settings: Settings,
    *,
    admin_password: str | None = None,
    database: str | None = None,
) -> Site:
    """Create the site's database, install the system tables and write its config.

    The site gets the built-in roles and an Administrator, who is a System Manager. Without
    a password the Administrator can't sign in until one is set.

    `database` installs into a database that already exists instead of creating one.
    Managed Postgres — Supabase, Neon, anything you don't own the server of — hands you one
    database and no right to make another, so that's the only way in. It must not already
    hold a site, and it is never dropped on failure: it wasn't ours to make.
    """
    if not SITE_NAME.match(name):
        raise SiteError(f"site name {name!r} must look like a hostname, e.g. acme.localhost")
    folder = settings.sites_dir / name
    if (folder / CONFIG_FILE).exists():
        raise SiteError(f"site {name!r} already exists")
    site = Site(name=name, db_name=database or db_name_for(name))
    if database is None:
        with _admin(settings) as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": site.db_name}
            ).first()
            if exists:
                raise SiteError(f"database {site.db_name!r} already exists on the server")
            conn.execute(text(f"CREATE DATABASE {_quote(conn, site.db_name)}"))
    elif _holds_a_site(site, settings):
        raise SiteError(f"database {database!r} already holds a Skemo site")
    try:
        engine = get_engine(site, settings)
        install_system_tables(engine)
        with engine.begin() as conn:
            install_roles(conn)
            create_user(
                conn,
                ADMIN,
                password=admin_password,
                full_name="Administrator",
                roles=[SYSTEM_MANAGER],
            )
    except Exception:
        if database is None:
            _drop_database(site, settings)  # only ever drop a database we made
        raise
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CONFIG_FILE).write_text(json.dumps({"db_name": site.db_name}, indent=2) + "\n")
    return site


def drop_site(name: str, settings: Settings) -> None:
    """Delete the site's database and folder."""
    site = load_site(name, settings)
    _drop_database(site, settings)
    shutil.rmtree(settings.sites_dir / name)
    if default_site_name(settings) == name:
        (settings.sites_dir / DEFAULT_SITE_FILE).unlink()


def configured_site(settings: Settings) -> Site | None:
    """The one site named by the environment, in single-site mode. None otherwise."""
    if not settings.site:
        return None
    return Site(name=settings.site, db_name=settings.site_db or db_name_for(settings.site))


def load_site(name: str, settings: Settings) -> Site:
    """The site by that name: from the environment in single-site mode, else its folder."""
    only = configured_site(settings)
    if only is not None:
        if name != only.name:
            raise SiteError(f"this server only serves {only.name!r}, not {name!r}")
        return only
    # Checking the pattern first also stops names like "../x" from escaping sites_dir.
    path = settings.sites_dir / name / CONFIG_FILE
    if not SITE_NAME.match(name) or not path.is_file():
        raise SiteError(f"no site named {name!r} in {settings.sites_dir}")
    config = json.loads(path.read_text())
    return Site(name=name, db_name=config["db_name"])


def list_sites(settings: Settings) -> list[str]:
    only = configured_site(settings)
    if only is not None:
        return [only.name]
    if not settings.sites_dir.is_dir():
        return []
    return sorted(p.parent.name for p in settings.sites_dir.glob(f"*/{CONFIG_FILE}"))


def set_default_site(name: str, settings: Settings) -> None:
    load_site(name, settings)  # raises if it doesn't exist
    (settings.sites_dir / DEFAULT_SITE_FILE).write_text(name + "\n")


def default_site_name(settings: Settings) -> str | None:
    only = configured_site(settings)
    if only is not None:
        return only.name
    path = settings.sites_dir / DEFAULT_SITE_FILE
    return (path.read_text().strip() or None) if path.is_file() else None


def _holds_a_site(site: Site, settings: Settings) -> bool:
    """True when that database already has Skemo's user table, so a site lives in it."""
    with get_engine(site, settings).connect() as conn:
        return inspect(conn).has_table("sys_user")


@contextmanager
def _admin(settings: Settings) -> Iterator[Connection]:
    # CREATE/DROP DATABASE can't run inside a transaction, hence AUTOCOMMIT.
    engine = create_engine(settings.db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def _drop_database(site: Site, settings: Settings) -> None:
    with _engines_lock:
        engine = _engines.pop((settings.db_url, site.db_name), None)
    if engine is not None:
        engine.dispose()
    with _admin(settings) as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {_quote(conn, site.db_name)} WITH (FORCE)"))


def _quote(conn: Connection, identifier: str) -> str:
    return conn.dialect.identifier_preparer.quote(identifier)
