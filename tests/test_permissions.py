"""Roles, per-DocType permissions, and the record layer enforcing them."""

import pytest
from factories import doctype, field
from sqlalchemy import insert

from skemo.auth import create_user, load_user
from skemo.doctypes import save_doctypes
from skemo.documents import DocumentNotFound, Records
from skemo.permissions import (
    SYSTEM_MANAGER,
    PermissionDenied,
    Permissions,
    grant,
    install_roles,
    revoke,
)
from skemo.system_tables import sys_role


@pytest.fixture
def crm(engine):
    save_doctypes(
        engine,
        [
            doctype("lead", field("lead_name", reqd=True), field("city")),
            doctype("report", field("title")),
        ],
    )
    with engine.begin() as conn:
        install_roles(conn)
        conn.execute(insert(sys_role), [{"name": "Sales"}, {"name": "Manager"}])
        create_user(conn, "asha@acme.in", password="a good password", roles=["Sales"])
        create_user(conn, "boss@acme.in", password="a good password", roles=[SYSTEM_MANAGER])
    return engine


@pytest.fixture
def records_for(crm):
    """records_for("asha@acme.in") -> a Records that checks permissions, as that user.

    Each call is its own transaction, like one request. Asking for the next one commits
    the one before, so what an earlier user wrote is really there for the next — don't keep
    using an earlier Records after that.
    """
    open_connection: list = []

    def close() -> None:
        for conn, transaction in open_connection:
            transaction.commit()
            conn.close()
        open_connection.clear()

    def make(login: str) -> Records:
        close()
        conn = crm.connect()
        open_connection.append((conn, conn.begin()))
        user = load_user(conn, login)
        assert user is not None, f"no user {login}"
        return Records.load(conn, user=user, check_permissions=True)

    yield make
    close()


def test_no_rule_means_no_access(crm, records_for):
    asha = records_for("asha@acme.in")
    with pytest.raises(PermissionDenied, match="You don't have access to Lead."):
        asha.list("lead")
    with pytest.raises(PermissionDenied, match="You can't create Lead."):
        asha.insert("lead", {"lead_name": "Walk-in"})


def test_a_system_manager_may_do_anything(crm, records_for):
    boss = records_for("boss@acme.in")
    assert boss.insert("lead", {"lead_name": "Walk-in"})["name"]
    assert boss.list("lead")["total"] == 1
    assert boss.permissions.admin


def test_granting_actions_one_at_a_time(crm, records_for):
    with crm.begin() as conn:
        grant(conn, "lead", "Sales", ["read"])
    asha = records_for("asha@acme.in")
    assert asha.list("lead")["total"] == 0  # reading is allowed now
    with pytest.raises(PermissionDenied, match="You can't create Lead."):
        asha.insert("lead", {"lead_name": "Walk-in"})

    with crm.begin() as conn:
        grant(conn, "lead", "Sales", ["read", "create", "write"])
    asha = records_for("asha@acme.in")
    lead = asha.insert("lead", {"lead_name": "Walk-in"})
    assert asha.update("lead", lead["name"], {"city": "Pune"})["city"] == "Pune"
    with pytest.raises(PermissionDenied, match="You can't delete Lead."):
        asha.delete("lead", lead["name"])


def test_if_owner_hides_other_peoples_records(crm, records_for):
    with crm.begin() as conn:
        grant(conn, "lead", SYSTEM_MANAGER, ["read", "create"])  # ignored: admins skip checks
        grant(conn, "lead", "Sales", ["read", "write", "create", "delete"], if_owner=True)
    boss = records_for("boss@acme.in")
    theirs = boss.insert("lead", {"lead_name": "Someone else's"})

    asha = records_for("asha@acme.in")
    mine = asha.insert("lead", {"lead_name": "Mine"})
    listed = asha.list("lead")
    assert [row["name"] for row in listed["data"]] == [mine["name"]]
    assert listed["total"] == 1  # the count matches what they can see

    # Someone else's record reads as missing, not as forbidden: the answer is the same
    # whether or not it exists.
    for call in (
        lambda: asha.get("lead", theirs["name"]),
        lambda: asha.update("lead", theirs["name"], {"city": "Pune"}),
        lambda: asha.delete("lead", theirs["name"]),
    ):
        with pytest.raises(DocumentNotFound):
            call()
    assert records_for("boss@acme.in").get("lead", theirs["name"])["lead_name"] == (
        "Someone else's"
    )


def test_two_roles_combine_to_the_most_generous(crm, records_for):
    with crm.begin() as conn:
        grant(conn, "lead", "Sales", ["read"], if_owner=True)
        grant(conn, "lead", "Manager", ["read", "delete"])
        from skemo.auth import set_roles

        set_roles(conn, "asha@acme.in", ["Sales", "Manager"])
        user = load_user(conn, "asha@acme.in")
        assert user is not None
        permissions = Permissions.for_user(conn, user)
    assert permissions.can("lead", "read") and permissions.can("lead", "delete")
    # Manager's rule isn't owner-limited, so the limit lifts.
    assert not permissions.owner_only("lead", "read")


def test_revoke_takes_access_away(crm, records_for):
    with crm.begin() as conn:
        grant(conn, "report", "Sales", ["read"])
    assert records_for("asha@acme.in").list("report")["total"] == 0
    with crm.begin() as conn:
        revoke(conn, "report", "Sales")
    with pytest.raises(PermissionDenied):
        records_for("asha@acme.in").list("report")


def test_grant_checks_its_arguments(crm, records_for):
    with crm.begin() as conn:
        with pytest.raises(ValueError, match="unknown action 'edit'"):
            grant(conn, "lead", "Sales", ["read", "edit"])  # type: ignore[list-item]
        with pytest.raises(ValueError, match="no role named 'Nobody'"):
            grant(conn, "lead", "Nobody", ["read"])
