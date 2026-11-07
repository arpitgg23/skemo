"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_URL = "postgresql+psycopg://postgres:postgres@localhost:5433/postgres"


@dataclass(frozen=True)
class Settings:
    # Admin connection to the Postgres server. Site databases are created through it, and
    # each site connects to its own database with the same credentials.
    db_url: str
    sites_dir: Path
    # Single-site mode: serve exactly this one site, taking its name and database from the
    # environment instead of a folder under sites_dir. It's what lets Skemo run somewhere
    # with no writable disk — a container, or a serverless function — and it's the only way
    # to use a managed Postgres that gives you one database you didn't create.
    site: str | None = None
    site_db: str | None = None
    # How long a session cookie lasts while it keeps being used.
    session_days: int = 14
    # Send the session cookie only over HTTPS. Off by default so http://localhost works;
    # set SKEMO_COOKIE_SECURE=1 anywhere the site is served over HTTPS.
    cookie_secure: bool = False
    # On: the builder may edit a DocType that belongs to an app, and writes the JSON back
    # to the app's folder for you to commit. Off: those DocTypes are read-only in the
    # browser, because the files are the truth.
    developer_mode: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_url=os.environ.get("SKEMO_DB_URL", DEFAULT_DB_URL),
            sites_dir=Path(os.environ.get("SKEMO_SITES_DIR", "sites")).resolve(),
            site=os.environ.get("SKEMO_SITE") or None,
            site_db=os.environ.get("SKEMO_SITE_DB") or None,
            session_days=int(os.environ.get("SKEMO_SESSION_DAYS", "14")),
            cookie_secure=os.environ.get("SKEMO_COOKIE_SECURE", "") not in ("", "0", "false"),
            developer_mode=os.environ.get("SKEMO_DEVELOPER_MODE", "") not in ("", "0", "false"),
        )
