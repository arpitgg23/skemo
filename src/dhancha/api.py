"""HTTP API. For now: health and read-only metadata. Record CRUD arrives in M2.

No authentication yet (that's M5), so don't expose this server to the internet.
"""

# No `from __future__ import annotations` here: FastAPI needs the Annotated dependencies
# below evaluated where they're defined, inside create_app.
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import inspect, select

from . import __version__
from .config import Settings
from .sites import Site, SiteError, default_site_name, get_engine, load_site
from .store import load_doctypes
from .system_tables import sys_schema_log

SITE_HEADER = "X-Dhancha-Site"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Dhancha", version=__version__)

    def current_site(request: Request) -> Site:
        """The X-Dhancha-Site header wins, then a site named like the host, then the default."""
        requested = request.headers.get(SITE_HEADER)
        if requested:
            try:
                return load_site(requested, settings)
            except SiteError as e:
                raise HTTPException(404, str(e)) from e
        for name in (request.url.hostname, default_site_name(settings)):
            if name:
                try:
                    return load_site(name, settings)
                except SiteError:
                    continue
        raise HTTPException(404, "No site found. Create one with: dhancha new-site <name>")

    CurrentSite = Annotated[Site, Depends(current_site)]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/meta/doctypes")
    def doctypes(site: CurrentSite) -> dict[str, Any]:
        """Every DocType's definition, plus the columns its table really has in Postgres."""
        engine = get_engine(site, settings)
        with engine.connect() as conn:
            definitions = load_doctypes(conn)
            inspector = inspect(conn)
            live = {
                dt.name: (
                    inspector.get_columns(dt.table_name)
                    if inspector.has_table(dt.table_name)
                    else []
                )
                for dt in definitions.values()
            }
        return {
            "site": site.name,
            "doctypes": [
                {
                    "name": dt.name,
                    "label": dt.label,
                    "module": dt.module,
                    "table": dt.table_name,
                    "fields": [f.model_dump(exclude_defaults=True) for f in dt.fields],
                    "columns": [
                        {
                            "name": c["name"],
                            "type": c["type"].compile(dialect=engine.dialect),
                            "nullable": c["nullable"],
                        }
                        for c in live[dt.name]
                    ],
                }
                for dt in definitions.values()
            ],
        }

    @app.get("/api/meta/schema-log")
    def schema_log(
        site: CurrentSite, limit: Annotated[int, Query(ge=1, le=500)] = 50
    ) -> dict[str, Any]:
        """The most recent DDL statements, newest first."""
        with get_engine(site, settings).connect() as conn:
            rows = conn.execute(
                select(sys_schema_log).order_by(sys_schema_log.c.id.desc()).limit(limit)
            ).mappings()
            entries = [
                {
                    "batch_id": str(r["batch_id"]),
                    "created_at": r["created_at"].isoformat(),
                    "actor": r["actor"],
                    "table": r["table_name"],
                    "statement": r["statement"],
                }
                for r in rows
            ]
        return {"site": site.name, "entries": entries}

    return app
