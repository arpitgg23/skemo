"""Command line: sites, DocTypes and the dev server."""

# No `from __future__ import annotations`: Typer reads these annotations at runtime.
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from .apps import AppError, installed_apps, load_app, record_app
from .auth import AuthError, create_user, find_user, roles_of, set_password, set_roles
from .config import Settings
from .doctypes import (
    DocTypeError,
    MigrationResult,
    delete_doctype,
    install_app,
    save_doctypes,
    sync_site,
)
from .meta import DocType
from .permissions import ACTIONS, grant, install_roles, revoke
from .sites import (
    SiteError,
    default_site_name,
    drop_site,
    get_engine,
    list_sites,
    load_site,
    new_site,
    set_default_site,
)
from .store import app_owners, load_doctypes, save_definition
from .sync import SchemaSyncError, SyncResult
from .system_tables import sys_permission, sys_role, sys_schema_log, sys_user

app = typer.Typer(help="Skemo: build ERPs and CRMs from metadata.", no_args_is_help=True)

SiteOption = Annotated[
    str | None,
    typer.Option("--site", "-s", help="Site to use (default: the one set with `skemo use`)."),
]
ConfirmOption = Annotated[bool, typer.Option("--yes", help="Confirm the destructive action.")]


@app.callback()
def main(
    sites_dir: Annotated[
        Path | None,
        typer.Option(help="Folder holding the sites (default: ./sites).", envvar="SKEMO_SITES_DIR"),
    ] = None,
) -> None:
    if sites_dir is not None:
        # The API server reads it too, including uvicorn's reload subprocess.
        os.environ["SKEMO_SITES_DIR"] = str(sites_dir.resolve())


@app.command("new-site")
def new_site_command(
    name: Annotated[str, typer.Argument(help="Site name, e.g. acme.localhost")],
    admin_password: Annotated[
        str | None,
        typer.Option(help="Administrator's password. Left out, one is generated and shown once."),
    ] = None,
    make_default: Annotated[
        bool, typer.Option("--default/--no-default", help="Make it the default site.")
    ] = True,
    database: Annotated[
        str | None,
        typer.Option(
            help="Install into this existing database instead of creating one. "
            "Managed Postgres (Supabase, Neon) only lets you use the database it gave you."
        ),
    ] = None,
) -> None:
    """Create a site: a new Postgres database with the system tables and an Administrator."""
    settings = Settings.from_env()
    generated = admin_password is None
    password = admin_password or secrets.token_urlsafe(12)
    with _errors():
        site = new_site(name, settings, admin_password=password, database=database)
        if make_default:
            set_default_site(name, settings)
    typer.echo(f"Created site {site.name} (database {site.db_name})")
    if generated:
        typer.secho(
            f"Administrator password: {password}\nWrite it down: it isn't shown again.",
            fg=typer.colors.YELLOW,
        )


@app.command("add-user")
def add_user_command(
    name: Annotated[str, typer.Argument(help="Login, usually an email")],
    password: Annotated[str | None, typer.Option(help="Leave out to set one later.")] = None,
    full_name: Annotated[str, typer.Option(help="Shown in the desk.")] = "",
    role: Annotated[
        list[str] | None, typer.Option("--role", help="Role to give them. Repeatable.")
    ] = None,
    site: SiteOption = None,
) -> None:
    """Add a user to a site."""
    with _errors(), _engine(site).begin() as conn:
        install_roles(conn)
        create_user(conn, name, password=password, full_name=full_name, roles=role or [])
    typer.echo(f"Added {name}" + (f" with roles: {', '.join(role)}" if role else ""))
    if not password:
        typer.echo(f"They can't sign in yet. Set a password: skemo set-password {name}")


@app.command("set-password")
def set_password_command(
    name: str,
    password: Annotated[str, typer.Option(prompt=True, hide_input=True, confirmation_prompt=True)],
    site: SiteOption = None,
) -> None:
    """Change a user's password. It signs them out everywhere."""
    with _errors(), _engine(site).begin() as conn:
        set_password(conn, name, password)
    typer.echo(f"Password changed for {name}")


@app.command("set-roles")
def set_roles_command(
    name: str,
    role: Annotated[list[str], typer.Option("--role", help="Repeatable; replaces their roles.")],
    site: SiteOption = None,
) -> None:
    """Replace a user's roles."""
    with _errors(), _engine(site).begin() as conn:
        stored = find_user(conn, name)
        if stored is None:
            raise AuthError(f"There's no user named {name!r}.")
        install_roles(conn)
        set_roles(conn, stored, role)
    typer.echo(f"{name} now has: {', '.join(role) or '(no roles)'}")


@app.command("grant")
def grant_command(
    doctype: str,
    role: Annotated[str, typer.Option(help='Role to grant, e.g. "Sales"')],
    actions: Annotated[
        str, typer.Option(help="Comma-separated: read,write,create,delete")
    ] = "read",
    if_owner: Annotated[
        bool, typer.Option("--if-owner", help="Only records the user owns.")
    ] = False,
    site: SiteOption = None,
) -> None:
    """Say what a role may do with a DocType. Replaces that role's existing rule."""
    wanted = [a.strip() for a in actions.split(",") if a.strip()]
    with _errors(), _engine(site).begin() as conn:
        install_roles(conn)
        if conn.execute(select(sys_role.c.name).where(sys_role.c.name == role)).first() is None:
            conn.execute(insert(sys_role).values(name=role))
            typer.echo(f"Created role {role}")
        grant(conn, doctype, role, wanted, if_owner=if_owner)  # type: ignore[arg-type]
    owner_note = " (only their own records)" if if_owner else ""
    typer.echo(f"{role} can {', '.join(wanted) or 'do nothing with'} {doctype}{owner_note}")


@app.command("revoke")
def revoke_command(doctype: str, role: str, site: SiteOption = None) -> None:
    """Take away a role's access to a DocType."""
    with _errors(), _engine(site).begin() as conn:
        revoke(conn, doctype, role)
    typer.echo(f"{role} can no longer use {doctype}")


@app.command("permissions")
def permissions_command(site: SiteOption = None) -> None:
    """Show who can do what on this site."""
    with _errors(), _engine(site).connect() as conn:
        users = conn.execute(
            select(sys_user.c.name, sys_user.c.enabled, sys_user.c.password).order_by(
                sys_user.c.name
            )
        ).all()
        roles = {row.name: sorted(roles_of(conn, row.name)) for row in users}
        rules = conn.execute(select(sys_permission).order_by(sys_permission.c.doctype)).all()
    typer.secho("Users", bold=True)
    for row in users:
        notes = [] if row.enabled else ["disabled"]
        if not row.password:
            notes.append("no password")
        suffix = f"  ({', '.join(notes)})" if notes else ""
        typer.echo(f"  {row.name}: {', '.join(roles[row.name]) or 'no roles'}{suffix}")
    typer.secho("Permissions", bold=True)
    if not rules:
        typer.echo("  none yet; System Managers can still do everything")
    for rule in rules:
        allowed = [a for a in ACTIONS if getattr(rule, f"can_{a}")]
        owner_note = " (own records only)" if rule.if_owner else ""
        typer.echo(f"  {rule.doctype}: {rule.role} can {', '.join(allowed)}{owner_note}")


@app.command("drop-site")
def drop_site_command(name: str, yes: ConfirmOption = False) -> None:
    """Delete a site and its entire database."""
    if not yes:
        _fail(f"this deletes site {name!r} and its entire database. Re-run with --yes to confirm.")
    with _errors():
        drop_site(name, Settings.from_env())
    typer.echo(f"Dropped site {name}")


@app.command("list-sites")
def list_sites_command() -> None:
    """List the sites in the sites folder."""
    settings = Settings.from_env()
    default = default_site_name(settings)
    names = list_sites(settings)
    if not names:
        typer.echo(f"No sites in {settings.sites_dir}")
    for name in names:
        typer.echo(f"{name}  (default)" if name == default else name)


@app.command("use")
def use_command(name: str) -> None:
    """Set the default site."""
    with _errors():
        set_default_site(name, Settings.from_env())
    typer.echo(f"Default site: {name}")


@app.command("save-doctype")
def save_doctype_command(
    paths: Annotated[list[Path], typer.Argument(help="DocType JSON files, or folders of them.")],
    site: SiteOption = None,
    rename: Annotated[
        list[str] | None,
        typer.Option(
            "--rename",
            help="A renamed field as old:new (doctype.old:new when saving several). Repeatable.",
        ),
    ] = None,
) -> None:
    """Create or update DocTypes from JSON, then sync their tables."""
    with _errors():
        doctypes = [_read_doctype(p) for p in _json_files(paths)]
        renames = _parse_renames(rename or [], doctypes)
        result = save_doctypes(_engine(site), doctypes, renames=renames)
    _report(result)


@app.command("delete-doctype")
def delete_doctype_command(name: str, site: SiteOption = None, yes: ConfirmOption = False) -> None:
    """Delete a DocType and drop its table (its rows are archived first)."""
    if not yes:
        _fail(f"this drops table tab_{name} (rows go to sys_archive first). Re-run with --yes.")
    with _errors():
        result = delete_doctype(_engine(site), name)
    _report(result)


@app.command("migrate")
def migrate_command(site: SiteOption = None) -> None:
    """Bring a site up to date: system tables, the installed apps' DocTypes, then the tables."""
    with _errors():
        result = sync_site(_engine(site))
    _report_migration(result)


@app.command("install-app")
def install_app_command(
    path: Annotated[Path, typer.Argument(help="The app folder, the one with app.json")],
    site: SiteOption = None,
) -> None:
    """Add an app to a site and apply the DocTypes it ships."""
    with _errors():
        installed, result = install_app(_engine(site), path)
    typer.echo(
        f"Installed {installed.title} ({installed.name} {installed.version}) from {installed.path}"
    )
    _report_migration(result)


@app.command("list-apps")
def list_apps_command(site: SiteOption = None) -> None:
    """Show the apps installed on a site."""
    with _errors(), _engine(site).connect() as conn:
        apps = installed_apps(conn)
        owners = app_owners(conn)
    if not apps:
        typer.echo("No apps installed. Add one with: skemo install-app <path>")
    for installed in apps:
        doctypes = sorted(name for name, owner in owners.items() if owner == installed.name)
        typer.echo(f"{installed.name} {installed.version}  {installed.path}")
        typer.echo(f"  DocTypes: {', '.join(doctypes) or 'none'}")
        if installed.missing:
            typer.secho("  the folder isn't there any more", fg=typer.colors.YELLOW)


@app.command("export-doctype")
def export_doctype_command(
    name: Annotated[str, typer.Argument(help="The DocType to write out")],
    to: Annotated[Path, typer.Option("--to", help="The app folder to write it into")],
    site: SiteOption = None,
) -> None:
    """Write a DocType made in the builder into an app's files, so it can go into git."""
    with _errors():
        target = load_app(to)
        engine = _engine(site)
        with engine.connect() as conn:
            doctype = load_doctypes(conn).get(name)
        if doctype is None:
            raise DocTypeError(f"there's no DocType named {name!r} on this site")
        path = target.write_doctype(doctype)
        with engine.begin() as conn:
            save_definition(conn, doctype, app=target.name)
            record_app(conn, target)
    typer.echo(f"Wrote {path}")
    typer.echo(f"{name} now belongs to {target.name}; commit the file to share it.")


@app.command("schema-log")
def schema_log_command(
    site: SiteOption = None,
    limit: Annotated[int, typer.Option(help="How many entries to show.")] = 20,
) -> None:
    """Show the most recent schema changes, oldest first."""
    with _errors(), _engine(site).connect() as conn:
        rows = (
            conn.execute(select(sys_schema_log).order_by(sys_schema_log.c.id.desc()).limit(limit))
            .mappings()
            .all()
        )
    if not rows:
        typer.echo("No schema changes yet.")
    for r in reversed(rows):
        statement = " ".join(r["statement"].split())
        typer.echo(f"{r['created_at']:%Y-%m-%d %H:%M:%S}  {r['actor']}  {statement}")


@app.command("serve")
def serve_command(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run("skemo.api:create_app", factory=True, host=host, port=port, reload=reload)


def _engine(site_name: str | None) -> Engine:
    settings = Settings.from_env()
    name = site_name or default_site_name(settings)
    if not name:
        raise SiteError("no site given and no default site: pass --site or run `skemo use`")
    return get_engine(load_site(name, settings), settings)


def _json_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.glob("*.json")) if path.is_dir() else [path])
    if not files:
        raise DocTypeError("no DocType JSON files found")
    return files


def _read_doctype(path: Path) -> DocType:
    try:
        return DocType.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise DocTypeError(f"can't read {path}: {e.strerror}") from e
    except ValidationError as e:
        problems = "\n".join(
            f"  {'.'.join(str(part) for part in err['loc']) or '(file)'}: {err['msg']}"
            for err in e.errors()
        )
        raise DocTypeError(f"{path} is not a valid DocType:\n{problems}") from e


def _parse_renames(specs: list[str], doctypes: list[DocType]) -> dict[str, dict[str, str]]:
    renames: dict[str, dict[str, str]] = {}
    for spec in specs:
        old, sep, new = spec.partition(":")
        if not (sep and old and new):
            raise DocTypeError(f"--rename {spec!r} should look like old:new")
        doctype, dot, fieldname = old.rpartition(".")
        if not dot:
            if len(doctypes) != 1:
                raise DocTypeError(f"--rename {spec!r}: say which DocType, e.g. lead.{old}:{new}")
            doctype, fieldname = doctypes[0].name, old
        renames.setdefault(doctype, {})[fieldname] = new
    return renames


def _report_migration(result: MigrationResult) -> None:
    if result.installed:
        typer.echo(f"Installed: {', '.join(result.installed)}")
    if result.apps:
        typer.echo(f"Apps read: {', '.join(result.apps)}")
    _report(result.sync)


def _report(result: SyncResult) -> None:
    if not result.changed:
        typer.echo("Tables already match the metadata. Nothing to change.")
        return
    for statement in result.statements:
        typer.echo(" ".join(statement.split()))
    typer.secho(f"{len(result.statements)} schema change(s) applied", fg=typer.colors.GREEN)
    if result.archived_rows:
        typer.echo(f"{result.archived_rows} value(s) copied to sys_archive before dropping")


@contextmanager
def _errors() -> Iterator[None]:
    """Turn expected failures into a one-line error instead of a traceback."""
    try:
        yield
    except (AppError, AuthError, DocTypeError, SchemaSyncError, SiteError, ValueError) as e:
        _fail(str(e))
    except IntegrityError as e:
        # A constraint we didn't check for: show the database's reason, not a traceback.
        _fail(str(e.orig or e).strip().splitlines()[0])
    except OperationalError as e:
        reason = str(e.orig or e).strip().splitlines()[0]
        _fail(f"can't reach Postgres ({reason}). Is it running? Try: docker compose up -d")


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)
