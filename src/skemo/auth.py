"""Who someone is: users, passwords and sessions.

Passwords are hashed with scrypt from the standard library, so there's no extra dependency
and no password is ever stored or logged in the clear. Signing in creates a session: the
browser keeps a random token in a cookie, and only the token's SHA-256 hash is stored, so
a copy of the database doesn't let anyone sign in as someone else.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection

from .system_tables import sys_role, sys_session, sys_user, sys_user_role

# scrypt, at the parameters Python's own documentation suggests for interactive logins.
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
SALT_BYTES = 16
SESSION_DAYS = 14
# A session that's been used in the last day isn't re-dated on every request.
REFRESH_AFTER = timedelta(hours=24)
ADMIN = "Administrator"


class AuthError(Exception):
    """The sign-in attempt failed. The message is safe to show."""


@dataclass(frozen=True)
class User:
    name: str
    full_name: str
    roles: frozenset[str]

    @property
    def label(self) -> str:
        return self.full_name or self.name


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise AuthError("A password needs at least 8 characters.")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **SCRYPT)
    except ValueError:
        return False
    # Constant time, so a wrong password can't be found one character at a time.
    return secrets.compare_digest(digest.hex(), digest_hex)


def create_user(
    conn: Connection,
    name: str,
    *,
    password: str | None = None,
    full_name: str = "",
    roles: list[str] | None = None,
) -> None:
    """Add a user. Without a password they exist but can't sign in yet."""
    name = name.strip()
    if not name:
        raise AuthError("A user needs a login name.")
    # Stored as typed (so "Administrator" stays tidy), but unique and matched ignoring case.
    if conn.execute(select(sys_user.c.name).where(_same_name(name))).first():
        raise AuthError(f"A user named {name!r} already exists.")
    conn.execute(
        insert(sys_user).values(
            name=name,
            full_name=full_name,
            password=hash_password(password) if password else None,
        )
    )
    set_roles(conn, name, roles or [])


def set_password(conn: Connection, name: str, password: str) -> None:
    hashed = hash_password(password)  # before the update, so a short password changes nothing
    stored = conn.execute(
        update(sys_user).where(_same_name(name)).values(password=hashed).returning(sys_user.c.name)
    ).scalar()
    if stored is None:
        raise AuthError(f"There's no user named {name!r}.")
    # Signing out everywhere is the point of changing a password.
    conn.execute(delete(sys_session).where(sys_session.c.user == stored))


def set_roles(conn: Connection, name: str, roles: list[str]) -> None:
    wanted = list(dict.fromkeys(roles))
    if wanted:
        known = set(
            conn.execute(select(sys_role.c.name).where(sys_role.c.name.in_(wanted))).scalars()
        )
        missing = [r for r in wanted if r not in known]
        if missing:
            raise AuthError(
                f"There's no role named {missing[0]!r}. Roles are made by granting them "
                f"something: skemo grant <doctype> --role {missing[0]} --actions read"
            )
    conn.execute(delete(sys_user_role).where(sys_user_role.c.user == name))
    if wanted:
        conn.execute(insert(sys_user_role), [{"user": name, "role": r} for r in wanted])


def find_user(conn: Connection, name: str) -> str | None:
    """The stored login for a typed name, ignoring case, or None."""
    return conn.execute(select(sys_user.c.name).where(_same_name(name))).scalar()


def roles_of(conn: Connection, name: str) -> frozenset[str]:
    rows = conn.execute(select(sys_user_role.c.role).where(sys_user_role.c.user == name)).scalars()
    return frozenset(rows)


def load_user(conn: Connection, name: str) -> User | None:
    row = conn.execute(
        select(sys_user.c.name, sys_user.c.full_name).where(
            sys_user.c.name == name, sys_user.c.enabled.is_(True)
        )
    ).first()
    if row is None:
        return None
    return User(name=row.name, full_name=row.full_name, roles=roles_of(conn, row.name))


def sign_in(conn: Connection, name: str, password: str) -> tuple[str, User]:
    """Check the password and start a session. Returns the cookie token and the user."""
    row = conn.execute(select(sys_user).where(_same_name(name.strip()))).first()
    # The same message either way, so nobody can use it to find out who has an account.
    if row is None or not row.enabled or not verify_password(password, row.password):
        raise AuthError("Wrong login or password.")
    name = row.name
    conn.execute(update(sys_user).where(sys_user.c.name == name).values(last_login_at=func.now()))
    token = secrets.token_urlsafe(32)
    conn.execute(
        insert(sys_session).values(
            token_hash=_token_hash(token),
            user=name,
            expires_at=datetime.now(UTC) + timedelta(days=SESSION_DAYS),
        )
    )
    return token, User(name=row.name, full_name=row.full_name, roles=roles_of(conn, name))


def resolve_session(conn: Connection, token: str | None) -> User | None:
    """The signed-in user for a cookie token, or None if it's missing, expired or unknown."""
    if not token:
        return None
    row = conn.execute(
        select(sys_session.c.user, sys_session.c.last_seen_at).where(
            sys_session.c.token_hash == _token_hash(token),
            sys_session.c.expires_at > func.now(),
        )
    ).first()
    if row is None:
        return None
    now = datetime.now(UTC)
    if now - row.last_seen_at > REFRESH_AFTER:
        # Staying active keeps you signed in; two weeks away signs you out.
        conn.execute(
            update(sys_session)
            .where(sys_session.c.token_hash == _token_hash(token))
            .values(last_seen_at=now, expires_at=now + timedelta(days=SESSION_DAYS))
        )
    return load_user(conn, row.user)


def sign_out(conn: Connection, token: str | None) -> None:
    if token:
        conn.execute(delete(sys_session).where(sys_session.c.token_hash == _token_hash(token)))


def purge_expired_sessions(conn: Connection) -> int:
    return conn.execute(delete(sys_session).where(sys_session.c.expires_at <= func.now())).rowcount


def _same_name(name: str):
    """Match a login ignoring case, so Asha@acme.in and asha@acme.in are one user."""
    return func.lower(sys_user.c.name) == name.strip().lower()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
