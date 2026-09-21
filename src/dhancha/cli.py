"""Command line: sites, DocTypes and the dev server."""

# No `from __future__ import annotations`: Typer reads these annotations at runtime.
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from .config import Settings
from .doctypes import DocTypeError, delete_doctype, save_doctypes, sync_site
from .meta import DocType
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
from .sync import SchemaSyncError, SyncResult
from .system_tables import sys_schema_log

app = typer.Typer(help="Dhancha: build ERPs and CRMs from metadata.", no_args_is_help=True)

SiteOption = Annotated[
    str | None,
    typer.Option("--site", "-s", help="Site to use (default: the one set with `dhancha use`)."),
]
ConfirmOption = Annotated[bool, typer.Option("--yes", help="Confirm the destructive action.")]


@app.callback()
def main(
    sites_dir: Annotated[
        Path | None,
        typer.Option(
            help="Folder holding the sites (default: ./sites).", envvar="DHANCHA_SITES_DIR"
        ),
    ] = None,
) -> None:
    if sites_dir is not None:
        # The API server reads it too, including uvicorn's reload subprocess.
        os.environ["DHANCHA_SITES_DIR"] = str(sites_dir.resolve())


@app.command("new-site")
def new_site_command(
    name: Annotated[str, typer.Argument(help="Site name, e.g. acme.localhost")],
    make_default: Annotated[
        bool, typer.Option("--default/--no-default", help="Make it the default site.")
    ] = True,
) -> None:
    """Create a site: a new Postgres database with the system tables."""
    settings = Settings.from_env()
    with _errors():
        site = new_site(name, settings)
        if make_default:
            set_default_site(name, settings)
    typer.echo(f"Created site {site.name} (database {site.db_name})")


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
    """Install missing system tables and re-sync every table from the stored metadata."""
    with _errors():
        installed, result = sync_site(_engine(site))
    if installed:
        typer.echo(f"Installed system tables: {', '.join(installed)}")
    _report(result)


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

    uvicorn.run("dhancha.api:create_app", factory=True, host=host, port=port, reload=reload)


def _engine(site_name: str | None) -> Engine:
    settings = Settings.from_env()
    name = site_name or default_site_name(settings)
    if not name:
        raise SiteError("no site given and no default site: pass --site or run `dhancha use`")
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
    except (DocTypeError, SchemaSyncError, SiteError) as e:
        _fail(str(e))
    except OperationalError as e:
        reason = str(e.orig or e).strip().splitlines()[0]
        _fail(f"can't reach Postgres ({reason}). Is it running? Try: docker compose up -d")


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)
