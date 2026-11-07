"""Sites: one Postgres database each, plus a config folder."""

from dataclasses import replace

import pytest
from sqlalchemy import inspect, text

from skemo.sites import (
    Site,
    SiteError,
    configured_site,
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


# Single-site mode: the one site comes from the environment, not from a folder. It's what
# lets Skemo run with no writable disk, and on a managed Postgres you were handed.


def test_single_site_mode_needs_no_folder_on_disk(settings):
    only = replace(settings, site="demo", site_db="neondb")

    assert configured_site(only) == Site(name="demo", db_name="neondb")
    assert load_site("demo", only).db_name == "neondb"
    assert list_sites(only) == ["demo"]
    assert default_site_name(only) == "demo"  # no currentsite.txt involved
    assert not only.sites_dir.exists()  # nothing was written anywhere


def test_single_site_mode_serves_that_site_and_no_other(settings):
    only = replace(settings, site="demo", site_db="neondb")
    with pytest.raises(SiteError, match="only serves 'demo', not 'acme'"):
        load_site("acme", only)


def test_without_a_site_db_the_name_still_decides_the_database(settings):
    assert configured_site(replace(settings, site="acme.in")).db_name == db_name_for("acme.in")
    assert configured_site(settings) is None  # not configured: ordinary folder mode


def test_a_site_can_be_installed_into_a_database_it_didnt_create(settings, admin_engine, site_name):
    # Stand in for the one database a managed Postgres hands you.
    given = f"given_{site_name.split('.')[0].replace('-', '_')}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{given}"'))
    try:
        site = new_site(site_name, settings, admin_password="a good password", database=given)

        assert site.db_name == given
        with get_engine(site, settings).connect() as conn:
            assert "sys_user" in set(inspect(conn).get_table_names())

        # It won't quietly install a second site over the first.
        with pytest.raises(SiteError, match="already holds a Skemo site"):
            new_site(f"other-{site_name}", settings, database=given)
    finally:
        get_engine(site, settings).dispose()
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{given}" WITH (FORCE)'))


def test_a_failed_install_never_drops_a_database_it_was_given(settings, admin_engine, site_name):
    given = f"kept_{site_name.split('.')[0].replace('-', '_')}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{given}"'))
    try:
        # Too short a password fails after the tables are installed.
        with pytest.raises(Exception, match="8 characters"):
            new_site(site_name, settings, admin_password="short", database=given)
        assert database_exists(admin_engine, given), "a database we were given must survive"
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{given}" WITH (FORCE)'))
