import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from dhancha.config import Settings
from dhancha.system_tables import install_system_tables

ADMIN_URL = os.environ.get(
    "DHANCHA_TEST_DB_URL", "postgresql+psycopg://postgres:postgres@localhost:5433/postgres"
)


@pytest.fixture(scope="session")
def admin_engine():
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.fail(
            f"Postgres isn't reachable at {make_url(ADMIN_URL).render_as_string()}. "
            "Start it with: docker compose up -d",
            pytrace=False,
        )
    yield engine
    engine.dispose()


@pytest.fixture
def engine(admin_engine):
    """A fresh, throwaway site database with the system tables installed."""
    name = f"dhancha_test_{uuid.uuid4().hex[:12]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    site_engine = create_engine(make_url(ADMIN_URL).set(database=name))
    install_system_tables(site_engine)
    yield site_engine
    site_engine.dispose()
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))


@pytest.fixture
def settings(tmp_path, admin_engine):
    return Settings(db_url=ADMIN_URL, sites_dir=tmp_path / "sites")


@pytest.fixture
def site_name():
    return f"test-{uuid.uuid4().hex[:8]}.localhost"
