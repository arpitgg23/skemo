"""Users, passwords and sessions."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, update

from dhancha.auth import (
    ADMIN,
    AuthError,
    create_user,
    find_user,
    hash_password,
    load_user,
    purge_expired_sessions,
    resolve_session,
    roles_of,
    set_password,
    set_roles,
    sign_in,
    sign_out,
    verify_password,
)
from dhancha.permissions import SYSTEM_MANAGER, install_roles
from dhancha.system_tables import sys_role, sys_session, sys_user


@pytest.fixture
def conn(engine):
    with engine.begin() as conn:
        install_roles(conn)
        create_user(conn, "asha@acme.in", password="correct horse", full_name="Asha")
        yield conn


def test_passwords_are_hashed_not_stored(conn):
    stored = conn.execute(select(sys_user.c.password)).scalar()
    assert stored is not None
    assert "correct horse" not in stored
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse", stored)
    assert not verify_password("Correct Horse", stored)
    # The same password hashes differently each time, because each has its own salt.
    assert hash_password("correct horse") != stored


@pytest.mark.parametrize("password", ["", "short12"])
def test_short_passwords_are_refused(conn, password):
    with pytest.raises(AuthError, match="at least 8 characters"):
        set_password(conn, "asha@acme.in", password)
    assert sign_in(conn, "asha@acme.in", "correct horse")  # the old one still works


def test_sign_in_starts_a_session_that_resolves_back(conn):
    token, user = sign_in(conn, "asha@acme.in", "correct horse")
    assert (user.name, user.label) == ("asha@acme.in", "Asha")
    assert resolve_session(conn, token) == user
    # Only the hash is stored, so the database never holds a usable token.
    assert conn.execute(select(sys_session.c.token_hash)).scalar() != token


def test_logins_ignore_case_but_passwords_do_not(conn):
    token, user = sign_in(conn, "  Asha@Acme.IN ", "correct horse")
    assert user.name == "asha@acme.in"  # as stored
    assert resolve_session(conn, token)
    with pytest.raises(AuthError, match="Wrong login or password"):
        sign_in(conn, "asha@acme.in", "Correct Horse")


@pytest.mark.parametrize("login", ["asha@acme.in", "nobody@acme.in"])
def test_a_wrong_password_says_the_same_for_any_login(conn, login):
    with pytest.raises(AuthError, match="^Wrong login or password.$"):
        sign_in(conn, login, "guessing")


def test_a_disabled_user_cannot_sign_in(conn):
    conn.execute(update(sys_user).where(sys_user.c.name == "asha@acme.in").values(enabled=False))
    with pytest.raises(AuthError, match="Wrong login or password"):
        sign_in(conn, "asha@acme.in", "correct horse")
    assert load_user(conn, "asha@acme.in") is None


def test_a_user_without_a_password_cannot_sign_in(conn):
    create_user(conn, "ravi@acme.in")
    with pytest.raises(AuthError, match="Wrong login or password"):
        sign_in(conn, "ravi@acme.in", "")
    set_password(conn, "ravi@acme.in", "a good password")
    assert sign_in(conn, "ravi@acme.in", "a good password")


def test_changing_a_password_signs_them_out_everywhere(conn):
    token, _ = sign_in(conn, "asha@acme.in", "correct horse")
    set_password(conn, "ASHA@acme.in", "a new password")
    assert resolve_session(conn, token) is None
    assert sign_in(conn, "asha@acme.in", "a new password")


def test_sign_out_and_unknown_tokens(conn):
    token, _ = sign_in(conn, "asha@acme.in", "correct horse")
    sign_out(conn, token)
    assert resolve_session(conn, token) is None
    assert resolve_session(conn, None) is None
    assert resolve_session(conn, "made up") is None


def test_expired_sessions_stop_working_and_can_be_purged(conn):
    token, _ = sign_in(conn, "asha@acme.in", "correct horse")
    conn.execute(update(sys_session).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    assert resolve_session(conn, token) is None
    assert purge_expired_sessions(conn) == 1


def test_duplicate_users_are_refused_ignoring_case(conn):
    with pytest.raises(AuthError, match="already exists"):
        create_user(conn, "ASHA@acme.in", password="another one")
    assert find_user(conn, "Asha@ACME.in") == "asha@acme.in"
    assert find_user(conn, "nobody@acme.in") is None


def test_an_unknown_role_is_a_clear_error(conn):
    with pytest.raises(AuthError, match="There's no role named 'Sales'"):
        set_roles(conn, "asha@acme.in", ["Sales"])
    with pytest.raises(AuthError, match="no role named"):
        create_user(conn, "ravi@acme.in", roles=["Sales"])
    assert roles_of(conn, "asha@acme.in") == frozenset()


def test_roles(conn):
    conn.execute(insert(sys_role).values(name="Sales"))
    assert roles_of(conn, "asha@acme.in") == frozenset()
    set_roles(conn, "asha@acme.in", ["Sales", SYSTEM_MANAGER])
    assert roles_of(conn, "asha@acme.in") == {"Sales", SYSTEM_MANAGER}
    set_roles(conn, "asha@acme.in", [])
    assert roles_of(conn, "asha@acme.in") == frozenset()


def test_a_new_site_has_an_administrator(settings, site_name):
    from dhancha.sites import drop_site, get_engine, new_site

    site = new_site(site_name, settings, admin_password="site admin pass")
    try:
        with get_engine(site, settings).begin() as conn:
            _, user = sign_in(conn, ADMIN, "site admin pass")
            assert user.roles == {SYSTEM_MANAGER}
    finally:
        drop_site(site_name, settings)
