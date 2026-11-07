"""What someone may do: roles, and one permission row per role and DocType.

A user's roles are looked up once per request and turned into a `Permissions` object the
record layer asks before every read or write. No permission row means no access, so a new
DocType is private until someone grants it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from .auth import ADMIN, User
from .system_tables import sys_permission, sys_role

Action = Literal["read", "write", "create", "delete"]
ACTIONS: tuple[Action, ...] = ("read", "write", "create", "delete")

# Every user has "All"; "System Manager" may do anything, including using the builder.
SYSTEM_MANAGER = "System Manager"
EVERYONE = "All"
BUILT_IN_ROLES = {
    SYSTEM_MANAGER: "Can design DocTypes and do anything with records.",
    EVERYONE: "Everyone has this role.",
}


class PermissionDenied(Exception):
    """This user may not do that."""


@dataclass(frozen=True)
class Rule:
    can: frozenset[Action]
    if_owner: bool  # only records whose owner is this user


@dataclass(frozen=True)
class Permissions:
    """One user's rules, for one site. `admin` skips every check."""

    user: str
    roles: frozenset[str]
    rules: dict[str, Rule]
    admin: bool = False

    @classmethod
    def for_user(cls, conn: Connection, user: User) -> Permissions:
        if SYSTEM_MANAGER in user.roles or user.name == ADMIN:
            return cls(user=user.name, roles=user.roles, rules={}, admin=True)
        roles = {*user.roles, EVERYONE}
        rows = conn.execute(select(sys_permission).where(sys_permission.c.role.in_(roles))).all()
        rules: dict[str, Rule] = {}
        for row in rows:
            allowed = {a for a in ACTIONS if getattr(row, f"can_{a}")}
            current = rules.get(row.doctype)
            # Several roles: the most generous row wins, and owner-only only sticks while
            # every row that grants access says so.
            rules[row.doctype] = Rule(
                can=frozenset(allowed | (current.can if current else set())),
                if_owner=row.if_owner and (current.if_owner if current else True),
            )
        return cls(user=user.name, roles=user.roles, rules=rules)

    def rule(self, doctype: str) -> Rule | None:
        return Rule(frozenset(ACTIONS), False) if self.admin else self.rules.get(doctype)

    def can(self, doctype: str, action: Action) -> bool:
        rule = self.rule(doctype)
        return rule is not None and action in rule.can

    def owner_only(self, doctype: str, action: Action) -> bool:
        """True when this user may do it, but only to records they own."""
        rule = self.rule(doctype)
        return rule is not None and action in rule.can and rule.if_owner

    def require(self, doctype: str, action: Action, label: str | None = None) -> None:
        if not self.can(doctype, action):
            what = label or doctype
            if action == "read":
                raise PermissionDenied(f"You don't have access to {what}.")
            raise PermissionDenied(f"You can't {action} {what}.")

    def require_admin(self, what: str = "do that") -> None:
        if not self.admin:
            raise PermissionDenied(f"Only a {SYSTEM_MANAGER} can {what}.")

    def readable(self, doctypes: Iterable[str]) -> list[str]:
        return [name for name in doctypes if self.can(name, "read")]


def install_roles(conn: Connection) -> None:
    """Make sure the built-in roles exist. Safe to run repeatedly."""
    stmt = insert(sys_role).values(
        [{"name": n, "description": d} for n, d in BUILT_IN_ROLES.items()]
    )
    conn.execute(stmt.on_conflict_do_nothing(index_elements=[sys_role.c.name]))


def grant(
    conn: Connection,
    doctype: str,
    role: str,
    actions: Iterable[Action],
    *,
    if_owner: bool = False,
) -> None:
    """Set what a role may do with a DocType, replacing any rule it already had."""
    unknown = set(actions) - set(ACTIONS)
    if unknown:
        raise ValueError(f"unknown action {sorted(unknown)[0]!r}; use: {', '.join(ACTIONS)}")
    if conn.execute(select(sys_role.c.name).where(sys_role.c.name == role)).first() is None:
        raise ValueError(f"there's no role named {role!r}")
    values = {f"can_{a}": a in set(actions) for a in ACTIONS}
    stmt = insert(sys_permission).values(doctype=doctype, role=role, if_owner=if_owner, **values)
    conn.execute(
        stmt.on_conflict_do_update(
            constraint="uq_sys_permission_doctype_role",
            set_={**values, "if_owner": if_owner},
        )
    )


def revoke(conn: Connection, doctype: str, role: str) -> None:
    conn.execute(
        delete(sys_permission).where(
            sys_permission.c.doctype == doctype, sys_permission.c.role == role
        )
    )
