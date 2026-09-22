"""HTTP API: records of any DocType under /api/resource, metadata under /api/meta.

No authentication yet (that's M5), so don't expose this server to the internet.
"""

# No `from __future__ import annotations` here: FastAPI needs the Annotated dependencies
# below evaluated where they're defined, inside create_app.
import json
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, select

from . import __version__
from .config import Settings
from .documents import DocumentConflict, DocumentNotFound, DocumentValidationError, Records
from .query import MAX_LIMIT, QueryError
from .sites import Site, SiteError, default_site_name, get_engine, load_site
from .store import load_doctypes
from .system_tables import sys_schema_log

SITE_HEADER = "X-Dhancha-Site"
ERROR_STATUS: dict[type[Exception], int] = {
    DocumentNotFound: 404,
    DocumentValidationError: 422,
    DocumentConflict: 409,
    QueryError: 400,
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Dhancha", version=__version__)
    for error_type, status in ERROR_STATUS.items():
        app.add_exception_handler(error_type, _error_handler(status))

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
    JsonBody = Annotated[dict[str, Any], Body()]

    @contextmanager
    def records(site: Site) -> Iterator[Records]:
        """One transaction per request: it commits only if the whole request succeeds."""
        with get_engine(site, settings).begin() as conn:
            yield Records.load(conn)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # Record routes return plain dicts (response_model=None) so FastAPI's encoder turns
    # Decimal amounts into JSON numbers rather than strings.

    @app.get("/api/resource/{doctype}", response_model=None)
    def list_records(
        doctype: str,
        site: CurrentSite,
        fields: str | None = None,
        filters: str | None = None,
        or_filters: str | None = None,
        order_by: str | None = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """List records. `fields`: comma-separated names or *; `filters` and `or_filters`: JSON
        (see query.py); `order_by`: e.g. "amount desc"."""
        with records(site) as r:
            return r.list(
                doctype,
                fields=_parse_fields(fields),
                filters=_parse_json(filters, "filters"),
                or_filters=_parse_json(or_filters, "or_filters"),
                order_by=order_by,
                limit=limit,
                offset=offset,
            )

    @app.post("/api/resource/{doctype}", status_code=201, response_model=None)
    def create_record(doctype: str, site: CurrentSite, data: JsonBody) -> dict[str, Any]:
        with records(site) as r:
            return {"data": r.insert(doctype, data)}

    # {name:path} because names may contain slashes, like invoice number INV/24-25/001.
    @app.get("/api/resource/{doctype}/{name:path}", response_model=None)
    def get_record(doctype: str, name: str, site: CurrentSite) -> dict[str, Any]:
        with records(site) as r:
            return {"data": r.get(doctype, name)}

    @app.put("/api/resource/{doctype}/{name:path}", response_model=None)
    def update_record(doctype: str, name: str, site: CurrentSite, data: JsonBody) -> dict[str, Any]:
        """Update the fields sent. Include `modified_at` from your read to catch clashing edits."""
        with records(site) as r:
            return {"data": r.update(doctype, name, data)}

    @app.delete("/api/resource/{doctype}/{name:path}", status_code=204)
    def delete_record(doctype: str, name: str, site: CurrentSite) -> Response:
        with records(site) as r:
            r.delete(doctype, name)
        return Response(status_code=204)

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
                    "autoname": dt.autoname,
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


def _error_handler(
    status: int,
) -> Callable[[Request, Exception], Coroutine[Any, Any, JSONResponse]]:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, DocumentValidationError):
            return JSONResponse(
                status_code=status, content={"detail": "Validation failed", "errors": exc.errors}
            )
        if isinstance(exc, DocumentConflict):
            return JSONResponse(status_code=status, content={"detail": str(exc), "code": exc.code})
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    return handle


def _parse_fields(raw: str | None) -> list[str] | None:
    """Accepts "a,b,c", "*" or a JSON list like ["a","b"]."""
    if raw is None:
        return None
    if raw.lstrip().startswith("["):
        fields = _parse_json(raw, "fields")
        if not (isinstance(fields, list) and all(isinstance(f, str) for f in fields)):
            raise QueryError("fields must be a list of field names")
        return fields
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_json(raw: str | None, what: str) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise QueryError(f"{what} isn't valid JSON: {e.msg}") from e
