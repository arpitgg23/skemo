"""Sites: one Postgres database each, plus a config folder."""

import pytest
from sqlalchemy import inspect, text

from dhancha.sites import (
    SiteError,
    db_name_for,
    default_site_name,
    drop_site,
    get_engine,
    list_sites,
    load_site,
    new_site,
    set_default_site,
)


def database_exists(admin_engine, name):
    with admin_engine.connect() as conn:
        query = text("SELECT 1 FROM pg_database WHERE datname = :n")
        return conn.execute(query, {"n": name}).first() is not None


def test_new_site_creates_a_database_with_system_tables(settings, admin_engine, site_name):
    site = new_site(site_name, settings)
    try:
        assert site.db_name == db_name_for(site_name)
        with get_engine(site, settings).connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert {"sys_doctype", "sys_schema_log", "sys_archive"} <= tables
        assert list_sites(settings) == [site_name]
        assert load_site(site_name, settings) == site
        with pytest.raises(SiteError, match="already exists"):
            new_site(site_name, settings)
    finally:
        drop_site(site_name, settings)
    assert not database_exists(admin_engine, site.db_name)
    assert list_sites(settings) == []


def test_default_site(settings, site_name):
    new_site(site_name, settings)
    try:
        assert default_site_name(settings) is None
        set_default_site(site_name, settings)
        assert default_site_name(settings) == site_name
    finally:
        drop_site(site_name, settings)
    assert default_site_name(settings) is None


@pytest.mark.parametrize("name", ["Acme", "acme_1", "../etc", "-acme", "acme.", ""])
def test_bad_site_names_are_rejected(settings, name):
    with pytest.raises(SiteError):
        new_site(name, settings)
    with pytest.raises(SiteError):
        load_site(name, settings)
