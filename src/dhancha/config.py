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
    # How long a session cookie lasts while it keeps being used.
    session_days: int = 14
    # Send the session cookie only over HTTPS. Off by default so http://localhost works;
    # set DHANCHA_COOKIE_SECURE=1 anywhere the site is served over HTTPS.
    cookie_secure: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_url=os.environ.get("DHANCHA_DB_URL", DEFAULT_DB_URL),
            sites_dir=Path(os.environ.get("DHANCHA_SITES_DIR", "sites")).resolve(),
            session_days=int(os.environ.get("DHANCHA_SESSION_DAYS", "14")),
            cookie_secure=os.environ.get("DHANCHA_COOKIE_SECURE", "") not in ("", "0", "false"),
        )
