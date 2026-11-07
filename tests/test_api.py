"""The HTTP API: health and read-only metadata."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from factories import doctype, field
from fastapi.testclient import TestClient
from sqlalchemy import insert

from skemo.api import SESSION_COOKIE, SITE_HEADER, create_app
from skemo.auth import ADMIN, create_user
from skemo.doctypes import install_app, save_doctype, save_doctypes
from skemo.permissions import grant, install_roles
from skemo.sites import drop_site, get_engine, new_site, set_default_site
from skemo.system_tables import sys_role

ADMIN_PASSWORD = "an admin password"


@pytest.fixture
def site(settings, site_name):
    site = new_site(site_name, settings, admin_password=ADMIN_PASSWORD)
    yield site
    drop_site(site_name, settings)


@pytest.fixture
def anonymous(settings):
    """A client that hasn't signed in."""
    return TestClient(create_app(settings))


@pytest.fixture
def client(settings, site):
    """Its own client, signed in as the Administrator, with the site header set."""
    client = TestClient(create_app(settings))
    client.headers[SITE_HEADER] = site.name
    response = client.post("/api/auth/login", json={"user": ADMIN, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return client


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_doctypes_show_metadata_and_live_columns(client, settings, site):
    save_doctype(
        get_engine(site, settings), doctype("lead", field("lead_name", reqd=True), module="CRM")
    )

    body = client.get("/api/meta/doctypes", headers={"X-Skemo-Site": site.name}).json()

    assert body["site"] == site.name
    [lead] = body["doctypes"]
    assert (lead["name"], lead["module"], lead["table"]) == ("lead", "CRM", "tab_lead")
    assert lead["fields"] == [
        {"fieldname": "lead_name", "label": "Lead Name", "fieldtype": "Data", "reqd": True}
    ]
    assert {"name": "lead_name", "type": "VARCHAR(140)", "nullable": True} in lead["columns"]


def test_schema_log_uses_the_default_site_without_a_header(client, settings, site):
    save_doctype(get_engine(site, settings), doctype("lead", field("lead_name")))
    set_default_site(site.name, settings)
    del client.headers[SITE_HEADER]  # fall back to the default site

    entries = client.get("/api/meta/schema-log").json()["entries"]

    assert entries[0]["table"] == "tab_lead"
    assert entries[0]["statement"].startswith("CREATE TABLE tab_lead")


def test_everything_but_health_needs_signing_in(anonymous, site):
    anonymous.headers[SITE_HEADER] = site.name
    assert anonymous.get("/api/health").status_code == 200
    for method, url in [
        ("get", "/api/meta/doctypes"),
        ("get", "/api/auth/me"),
        ("get", "/api/resource/lead"),
        ("get", "/api/meta/schema-log"),
        ("post", "/api/resource/lead"),
        ("put", "/api/meta/doctype/lead"),
        ("delete", "/api/meta/doctype/lead"),
    ]:
        body = {"json": {}} if method in ("post", "put") else {}
        response = getattr(anonymous, method)(url, **body)
        assert response.status_code == 401, f"{method} {url} was {response.status_code}"


def test_sign_in_and_out(anonymous, site):
    anonymous.headers[SITE_HEADER] = site.name
    wrong = anonymous.post("/api/auth/login", json={"user": ADMIN, "password": "guessing"})
    assert (wrong.status_code, wrong.json()["detail"]) == (401, "Wrong login or password.")

    ok = anonymous.post(
        "/api/auth/login", json={"user": "administrator", "password": ADMIN_PASSWORD}
    )
    assert ok.status_code == 200
    assert ok.json()["user"] == {
        "name": ADMIN,
        "full_name": ADMIN,
        "label": ADMIN,
        "roles": ["System Manager"],
        "is_admin": True,
    }
    cookie = ok.cookies[SESSION_COOKIE]
    assert "httponly" in ok.headers["set-cookie"].lower()  # JavaScript can't read it
    assert anonymous.get("/api/auth/me").json()["user"]["name"] == ADMIN

    signed_out = anonymous.post("/api/auth/logout")
    assert signed_out.status_code == 204
    # The browser is told to drop the cookie, and the session is gone server-side too.
    assert SESSION_COOKIE in signed_out.headers.get("set-cookie", "")
    assert SESSION_COOKIE not in anonymous.cookies
    assert anonymous.get("/api/auth/me").status_code == 401
    # The old token is dead even if someone kept a copy of it.
    anonymous.cookies.set(SESSION_COOKIE, cookie)
    assert anonymous.get("/api/auth/me").status_code == 401


def test_a_user_sees_only_what_their_roles_allow(client, anonymous, settings, site):
    save_doctypes(
        get_engine(site, settings),
        [doctype("lead", field("lead_name"), module="CRM"), doctype("payslip", field("net_pay"))],
    )
    with get_engine(site, settings).begin() as conn:
        install_roles(conn)
        conn.execute(insert(sys_role).values(name="Sales"))
        create_user(conn, "asha@acme.in", password="a good password", roles=["Sales"])
        grant(conn, "lead", "Sales", ["read", "create"])

    anonymous.headers[SITE_HEADER] = site.name
    anonymous.post("/api/auth/login", json={"user": "asha@acme.in", "password": "a good password"})

    me = anonymous.get("/api/auth/me").json()["user"]
    assert (me["roles"], me["is_admin"]) == (["Sales"], False)

    listed = anonymous.get("/api/meta/doctypes").json()["doctypes"]
    assert [dt["name"] for dt in listed] == ["lead"]  # payslip isn't even mentioned
    assert listed[0]["can"] == ["create", "read"]

    assert anonymous.get("/api/resource/lead").status_code == 200
    assert anonymous.get("/api/resource/payslip").status_code == 403
    deleting = anonymous.delete("/api/resource/lead/whatever")
    assert (deleting.status_code, deleting.json()["detail"]) == (403, "You can't delete Lead.")

    # The builder is for System Managers only.
    for response in (
        anonymous.put("/api/meta/doctype/lead", json={"doctype": {"name": "lead", "fields": []}}),
        anonymous.delete("/api/meta/doctype/lead"),
        anonymous.get("/api/meta/schema-log"),
    ):
        assert response.status_code == 403
        assert "System Manager" in response.json()["detail"]
    # …and the Administrator's own save still works, so nothing was broken for them.
    assert client.get("/api/meta/schema-log").status_code == 200


@pytest.mark.parametrize("name", ["nope.localhost", "../../etc"])
def test_unknown_site_is_a_404(client, name):
    response = client.get("/api/meta/doctypes", headers={"X-Skemo-Site": name})
    assert response.status_code == 404


@pytest.fixture
def crm_client(client, settings, site):
    save_doctypes(
        get_engine(site, settings),
        [
            doctype(
                "customer",
                field("customer_name", reqd=True, in_list_view=True),
                field("credit_limit", "Currency"),
                autoname="format:CUST-{####}",
            ),
            doctype(
                "invoice",
                field("customer", "Link", options="customer"),
                field("total", "Currency"),
                autoname="prompt",
            ),
        ],
    )
    client.headers[SITE_HEADER] = site.name
    return client


def test_records_crud_over_http(crm_client):
    created = crm_client.post(
        "/api/resource/customer", json={"customer_name": "Acme", "credit_limit": 50000}
    )
    assert created.status_code == 201
    doc = created.json()["data"]
    assert (doc["name"], doc["credit_limit"]) == ("CUST-0001", 50000)

    assert crm_client.get("/api/resource/customer/CUST-0001").json()["data"]["customer_name"] == (
        "Acme"
    )

    updated = crm_client.put(
        "/api/resource/customer/CUST-0001",
        json={"credit_limit": "75000.50", "modified_at": doc["modified_at"]},
    )
    assert updated.json()["data"]["credit_limit"] == 75000.5

    listed = crm_client.get(
        "/api/resource/customer",
        params={
            "fields": "customer_name,credit_limit",
            "filters": '[["credit_limit", ">", 60000]]',
        },
    )
    assert listed.json() == {
        "data": [{"name": "CUST-0001", "customer_name": "Acme", "credit_limit": 75000.5}],
        "total": 1,
    }

    assert crm_client.delete("/api/resource/customer/CUST-0001").status_code == 204
    assert crm_client.get("/api/resource/customer/CUST-0001").status_code == 404


def test_builder_saves_a_doctype_and_returns_the_ddl(client, site):
    client.headers[SITE_HEADER] = site.name
    visit = {
        "name": "visit",
        "module": "Field Sales",
        "fields": [
            {"fieldname": "phone", "fieldtype": "Data"},
            {"fieldname": "notes", "fieldtype": "Text"},
        ],
    }
    created = client.put("/api/meta/doctype/visit", json={"doctype": visit, "create": True})
    assert created.status_code == 200
    assert created.json()["statements"][0].startswith("CREATE TABLE tab_visit")

    again = client.put("/api/meta/doctype/visit", json={"doctype": visit, "create": True})
    assert again.status_code == 409  # "create" never overwrites

    visit["fields"][0]["fieldname"] = "mobile"
    renamed = client.put(
        "/api/meta/doctype/visit", json={"doctype": visit, "renames": {"phone": "mobile"}}
    )
    assert [s for s in renamed.json()["statements"]] == [
        "ALTER TABLE tab_visit RENAME phone TO mobile"
    ]

    [listed] = client.get("/api/meta/doctypes").json()["doctypes"]
    assert [f["fieldname"] for f in listed["fields"]] == ["mobile", "notes"]


def test_builder_errors_point_at_the_field(client, site):
    client.headers[SITE_HEADER] = site.name
    bad = {
        "name": "visit",
        "fields": [
            {"fieldname": "ok", "fieldtype": "Data"},
            {"fieldname": "Bad Name", "fieldtype": "Data"},
        ],
    }
    response = client.put("/api/meta/doctype/visit", json={"doctype": bad})
    assert response.status_code == 422
    [error] = response.json()["errors"]
    assert error["loc"] == ["fields", 1]
    assert error["message"].startswith("fieldname 'Bad Name' must start with a letter")

    unknown_link = {
        "name": "visit",
        "fields": [{"fieldname": "c", "fieldtype": "Link", "options": "nothing"}],
    }
    response = client.put("/api/meta/doctype/visit", json={"doctype": unknown_link})
    assert (response.status_code, response.json()["detail"]) == (
        422,
        "visit.c links to unknown DocType 'nothing'",
    )


def test_builder_refuses_changes_the_data_does_not_allow(crm_client):
    crm_client.post("/api/resource/customer", json={"customer_name": "Acme"})
    customer = crm_client.get("/api/meta/doctypes").json()["doctypes"][0]
    fields = [
        f | {"fieldtype": "Int"} if f["fieldname"] == "customer_name" else f
        for f in customer["fields"]
    ]
    definition = {"name": "customer", "autoname": customer["autoname"], "fields": fields}
    response = crm_client.put("/api/meta/doctype/customer", json={"doctype": definition})
    assert response.status_code == 409  # 'Acme' isn't a number; nothing changed
    assert "tab_customer" in response.json()["detail"]


def test_builder_deletes_doctypes(crm_client):
    assert crm_client.delete("/api/meta/doctype/nothing").status_code == 404
    linked = crm_client.delete("/api/meta/doctype/customer")
    assert linked.status_code == 409  # the invoice DocType links to it
    deleted = crm_client.delete("/api/meta/doctype/invoice")
    assert deleted.json()["statements"] == ["DROP TABLE tab_invoice"]


@pytest.fixture
def app_site(client, settings, site, tmp_path):
    """A site with a small app installed, and the admin client for it."""
    folder = tmp_path / "field"
    (folder / "doctypes").mkdir(parents=True)
    (folder / "app.json").write_text('{"name": "field", "title": "Field", "version": "1.0.0"}')
    definition = {
        "name": "visit",
        "fields": [{"fieldname": "customer_name", "fieldtype": "Data", "label": "Customer"}],
    }
    (folder / "doctypes" / "visit.json").write_text(json.dumps(definition))
    install_app(get_engine(site, settings), folder)
    return folder, definition


def test_an_apps_doctype_is_read_only_in_the_builder(client, app_site):
    folder, definition = app_site
    listed = client.get("/api/meta/doctypes").json()["doctypes"]
    [visit] = [dt for dt in listed if dt["name"] == "visit"]
    assert visit["app"] == "field"

    definition["fields"][0]["label"] = "Client"
    refused = client.put("/api/meta/doctype/visit", json={"doctype": definition})
    assert refused.status_code == 409
    assert "belongs to the app 'field'" in refused.json()["detail"]
    assert client.delete("/api/meta/doctype/visit").status_code == 409
    # The file is untouched, so `skemo migrate` would not undo anything.
    assert "Customer" in (folder / "doctypes" / "visit.json").read_text()


def test_developer_mode_saves_and_writes_the_file_back(settings, site, app_site):
    folder, definition = app_site
    developer = TestClient(create_app(replace(settings, developer_mode=True)))
    developer.headers[SITE_HEADER] = site.name
    developer.post("/api/auth/login", json={"user": ADMIN, "password": ADMIN_PASSWORD})
    assert developer.get("/api/auth/me").json()["developer_mode"] is True

    definition["fields"].append({"fieldname": "notes", "fieldtype": "Text"})
    saved = developer.put("/api/meta/doctype/visit", json={"doctype": definition})

    assert saved.status_code == 200
    assert saved.json()["statements"] == ["ALTER TABLE tab_visit ADD COLUMN notes TEXT"]
    written = Path(saved.json()["written_to"])
    assert written == folder / "doctypes" / "visit.json"
    # The file now has the new field, ready to commit.
    assert [f["fieldname"] for f in json.loads(written.read_text())["fields"]] == [
        "customer_name",
        "notes",
    ]


def test_http_errors(crm_client):
    invalid = crm_client.post("/api/resource/customer", json={"credit_limit": "lots"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Validation failed"
    assert {e["field"] for e in invalid.json()["errors"]} == {"customer_name", "credit_limit"}

    crm_client.post("/api/resource/customer", json={"customer_name": "Acme"})
    crm_client.post(
        "/api/resource/invoice", json={"name": "INV/24-25/001", "customer": "CUST-0001"}
    )
    invoice = crm_client.get("/api/resource/invoice/INV/24-25/001")  # slashes in names work
    assert invoice.json()["data"]["customer"] == "CUST-0001"

    linked = crm_client.delete("/api/resource/customer/CUST-0001")
    assert (linked.status_code, linked.json()["code"]) == (409, "linked")
    assert crm_client.get("/api/resource/customer", params={"filters": "{oops"}).status_code == 400
    assert crm_client.get("/api/resource/nothing").status_code == 404


def test_a_printed_page_names_the_record_a_link_points_at(crm_client):
    crm_client.post("/api/resource/customer", json={"customer_name": "Asha Textiles"})
    crm_client.post("/api/resource/invoice", json={"name": "INV-001", "customer": "CUST-0001"})

    page = crm_client.get("/api/print/invoice/INV-001")

    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    # The customer reads as a customer, not as the code the invoice links by.
    assert "Asha Textiles" in page.text
    assert "CUST-0001" not in page.text
