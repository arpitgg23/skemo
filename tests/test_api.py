"""The HTTP API: health and read-only metadata."""

import pytest
from factories import doctype, field
from fastapi.testclient import TestClient

from dhancha.api import SITE_HEADER, create_app
from dhancha.doctypes import save_doctype, save_doctypes
from dhancha.sites import drop_site, get_engine, new_site, set_default_site


@pytest.fixture
def site(settings, site_name):
    site = new_site(site_name, settings)
    yield site
    drop_site(site_name, settings)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_doctypes_show_metadata_and_live_columns(client, settings, site):
    save_doctype(
        get_engine(site, settings), doctype("lead", field("lead_name", reqd=True), module="CRM")
    )

    body = client.get("/api/meta/doctypes", headers={"X-Dhancha-Site": site.name}).json()

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

    entries = client.get("/api/meta/schema-log").json()["entries"]

    assert entries[0]["table"] == "tab_lead"
    assert entries[0]["statement"].startswith("CREATE TABLE tab_lead")


@pytest.mark.parametrize("name", ["nope.localhost", "../../etc"])
def test_unknown_site_is_a_404(client, name):
    response = client.get("/api/meta/doctypes", headers={"X-Dhancha-Site": name})
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
