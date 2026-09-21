"""The HTTP API: health and read-only metadata."""

import pytest
from factories import doctype, field
from fastapi.testclient import TestClient

from dhancha.api import create_app
from dhancha.doctypes import save_doctype
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
