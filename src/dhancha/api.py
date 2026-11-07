"""HTTP API: records of any DocType under /api/resource, metadata under /api/meta.

Everything except /api/health and signing in needs a session cookie, and every record
operation is checked against the user's permissions.
"""

# No `from __future__ import annotations` here: FastAPI needs the Annotated dependencies
# below evaluated where they're defined, inside create_app.
import json
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import inspect, select

from . import __version__
from .apps import AppError, app_of
from .auth import AuthError, User, resolve_session, sign_in, sign_out
from .config import Settings
from .doctypes import DocTypeError, delete_doctype, save_doctypes
from .documents import DocumentConflict, DocumentNotFound, DocumentValidationError, Records
from .meta import DocType
from .permissions import ACTIONS, PermissionDenied, Permissions
from .query import MAX_LIMIT, QueryError
from .sites import Site, SiteError, default_site_name, get_engine, load_site
from .store import app_owners, load_doctypes
from .sync import SchemaSyncError
from .system_tables import sys_schema_log

SITE_HEADER = "X-Dhancha-Site"
SESSION_COOKIE = "dhancha_session"
ERROR_STATUS: dict[type[Exception], int] = {
    DocumentNotFound: 404,
    DocumentValidationError: 422,
    DocumentConflict: 409,
    QueryError: 400,
    DocTypeError: 422,  # e.g. a Link to a DocType that doesn't exist, or a bad rename
    SchemaSyncError: 409,  # the live data doesn't allow the change; nothing was applied
    PermissionDenied: 403,
    AuthError: 401,
    AppError: 409,  # an app's folder has moved or has a file that isn't a valid DocType
}


class DocTypeSave(BaseModel):
    """What the builder sends when it saves a DocType."""

    doctype: dict[str, Any]  # validated as a DocType in the route, to report errors per field
    renames: dict[str, str] = {}  # old fieldname -> new, so renamed columns keep their data
    create: bool = False  # refuse to overwrite a DocType that already exists


class Credentials(BaseModel):
    user: str
    password: str


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

    def current_user(request: Request, site: CurrentSite) -> User:
        """The signed-in user, from the session cookie."""
        with get_engine(site, settings).begin() as conn:
            user = resolve_session(conn, request.cookies.get(SESSION_COOKIE))
        if user is None:
            raise HTTPException(401, "Please sign in.")
        return user

    SignedIn = Annotated[User, Depends(current_user)]
    JsonBody = Annotated[dict[str, Any], Body()]

    @contextmanager
    def records(site: Site, user: User) -> Iterator[Records]:
        """One transaction per request: it commits only if the whole request succeeds."""
        with get_engine(site, settings).begin() as conn:
            yield Records.load(conn, user=user, check_permissions=True)

    def require_admin(site: Site, user: User, what: str) -> None:
        """Raises unless this user may do work reserved for a System Manager."""
        with get_engine(site, settings).connect() as conn:
            Permissions.for_user(conn, user).require_admin(what)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/api/auth/login", response_model=None)
    def login(site: CurrentSite, credentials: Credentials, response: Response) -> dict[str, Any]:
        with get_engine(site, settings).begin() as conn:
            token, user = sign_in(conn, credentials.user, credentials.password)
            permissions = Permissions.for_user(conn, user)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=settings.session_days * 24 * 3600,
            httponly=True,  # JavaScript can't read it, so a script injection can't steal it
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )
        # The same shape as /api/auth/me, so the desk can use it as the session straight away.
        return _session(site, user, permissions, settings)

    @app.post("/api/auth/logout", status_code=204)
    def logout(site: CurrentSite, request: Request) -> Response:
        with get_engine(site, settings).begin() as conn:
            sign_out(conn, request.cookies.get(SESSION_COOKIE))
        # The cookie has to be cleared on the response that's actually returned.
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/auth/me", response_model=None)
    def me(site: CurrentSite, user: SignedIn) -> dict[str, Any]:
        """Who's signed in and what they may do, so the desk shows only what they can use."""
        with get_engine(site, settings).connect() as conn:
            permissions = Permissions.for_user(conn, user)
        return _session(site, user, permissions, settings)

    # Record routes return plain dicts (response_model=None) so FastAPI's encoder turns
    # Decimal amounts into JSON numbers rather than strings.

    @app.get("/api/resource/{doctype}", response_model=None)
    def list_records(
        doctype: str,
        site: CurrentSite,
        user: SignedIn,
        fields: str | None = None,
        filters: str | None = None,
        or_filters: str | None = None,
        order_by: str | None = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """List records. `fields`: comma-separated names or *; `filters` and `or_filters`: JSON
        (see query.py); `order_by`: e.g. "amount desc"."""
        with records(site, user) as r:
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
    def create_record(
        doctype: str, site: CurrentSite, user: SignedIn, data: JsonBody
    ) -> dict[str, Any]:
        with records(site, user) as r:
            return {"data": r.insert(doctype, data)}

    # {name:path} because names may contain slashes, like invoice number INV/24-25/001.
    @app.get("/api/resource/{doctype}/{name:path}", response_model=None)
    def get_record(doctype: str, name: str, site: CurrentSite, user: SignedIn) -> dict[str, Any]:
        with records(site, user) as r:
            return {"data": r.get(doctype, name)}

    @app.put("/api/resource/{doctype}/{name:path}", response_model=None)
    def update_record(
        doctype: str, name: str, site: CurrentSite, user: SignedIn, data: JsonBody
    ) -> dict[str, Any]:
        """Update the fields sent. Include `modified_at` from your read to catch clashing edits."""
        with records(site, user) as r:
            return {"data": r.update(doctype, name, data)}

    @app.delete("/api/resource/{doctype}/{name:path}", status_code=204)
    def delete_record(doctype: str, name: str, site: CurrentSite, user: SignedIn) -> Response:
        with records(site, user) as r:
            r.delete(doctype, name)
        return Response(status_code=204)

    @app.get("/api/meta/doctypes")
    def doctypes(site: CurrentSite, user: SignedIn) -> dict[str, Any]:
        """The DocTypes this user may read, with the columns their tables really have."""
        engine = get_engine(site, settings)
        with engine.connect() as conn:
            permissions = Permissions.for_user(conn, user)
            stored = load_doctypes(conn)
            definitions = {name: dt for name, dt in stored.items() if permissions.can(name, "read")}
            # A child DocType has no rules of its own: it comes along with each record
            # that holds its rows, so the desk can draw that grid.
            for dt in list(definitions.values()):
                for child in sorted(dt.children()):
                    if child in stored:
                        definitions.setdefault(child, stored[child])
            owners = app_owners(conn)
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
                    "description": dt.description,
                    "app": owners.get(dt.name),  # None when the builder made it
                    "is_child": dt.is_child,
                    "can": sorted(a for a in ACTIONS if permissions.can(dt.name, a)),
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
        site: CurrentSite, user: SignedIn, limit: Annotated[int, Query(ge=1, le=500)] = 50
    ) -> dict[str, Any]:
        """The most recent DDL statements, newest first. System Managers only."""
        require_admin(site, user, "see the schema log")
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

    @app.put("/api/meta/doctype/{name}", response_model=None)
    def save_doctype_route(name: str, site: CurrentSite, user: SignedIn, body: DocTypeSave) -> Any:
        """Create or update a DocType and sync its table, returning the DDL that ran."""
        require_admin(site, user, "design DocTypes")
        try:
            doctype = DocType.model_validate(body.doctype)
        except ValidationError as e:
            errors = [
                {"loc": list(err["loc"]), "message": err["msg"].removeprefix("Value error, ")}
                for err in e.errors()
            ]
            return JSONResponse(
                status_code=422, content={"detail": "The DocType isn't valid", "errors": errors}
            )
        if doctype.name != name:
            raise HTTPException(422, f"the URL says {name!r} but the DocType is {doctype.name!r}")
        engine = get_engine(site, settings)
        with engine.connect() as conn:
            if body.create and name in load_doctypes(conn):
                raise HTTPException(409, f"a DocType named {name!r} already exists")
            owner = app_owners(conn).get(name)
            app = app_of(conn, name) if owner and settings.developer_mode else None
        if owner and not settings.developer_mode:
            raise HTTPException(
                409,
                f"{name!r} belongs to the app {owner!r}, so its file is the truth. Edit "
                f"{owner}/doctypes/{name}.json and run `dhancha migrate`, or start the "
                "server with DHANCHA_DEVELOPER_MODE=1 to edit it here and write the file.",
            )
        result = save_doctypes(engine, [doctype], renames={name: body.renames}, app=owner)
        written = str(app.write_doctype(doctype)) if app else None
        return {
            "doctype": doctype.model_dump(mode="json", exclude_defaults=True),
            "statements": result.statements,
            "archived_rows": result.archived_rows,
            "written_to": written,
        }

    @app.delete("/api/meta/doctype/{name}", response_model=None)
    def delete_doctype_route(name: str, site: CurrentSite, user: SignedIn) -> Any:
        """Delete a DocType and drop its table (its rows are archived first)."""
        require_admin(site, user, "delete DocTypes")
        engine = get_engine(site, settings)
        with engine.connect() as conn:
            if name not in load_doctypes(conn):
                raise HTTPException(404, f"no DocType named {name!r}")
            owner = app_owners(conn).get(name)
        if owner:
            raise HTTPException(
                409,
                f"{name!r} belongs to the app {owner!r}. Delete "
                f"{owner}/doctypes/{name}.json and run `dhancha migrate` instead.",
            )
        try:
            result = delete_doctype(engine, name)
        except DocTypeError as e:  # other DocTypes link to it
            return JSONResponse(status_code=409, content={"detail": str(e)})
        return {"statements": result.statements, "archived_rows": result.archived_rows}

    return app


def _session(
    site: Site, user: User, permissions: Permissions, settings: Settings
) -> dict[str, Any]:
    return {
        "site": site.name,
        # Developer mode lets the builder edit an app's DocTypes and write their files.
        "developer_mode": settings.developer_mode,
        "user": {
            "name": user.name,
            "full_name": user.full_name,
            "label": user.label,
            "roles": sorted(user.roles),
            "is_admin": permissions.admin,
        },
    }


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
