"""Records: create, read, update, delete and list, checked against the DocType's fields."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from factories import RecordsClient, doctype, field, rows

from skemo.doctypes import save_doctypes
from skemo.documents import DocumentConflict, DocumentNotFound, DocumentValidationError
from skemo.query import QueryError

TODAY = date(2026, 9, 21)


@pytest.fixture
def crm(engine):
    """A tiny CRM: customers, and deals that link to them."""
    save_doctypes(
        engine,
        [
            doctype(
                "customer",
                field("customer_name", reqd=True, in_list_view=True),
                field("email", unique=True),
                field("city", in_list_view=True),
                autoname="format:CUST-{####}",
            ),
            doctype(
                "deal",
                field("title", reqd=True, in_list_view=True),
                field("customer", "Link", options="customer"),
                field(
                    "stage", "Select", options="Open\nWon\nLost", default="Open", in_list_view=True
                ),
                field("amount", "Currency"),
                field("probability", "Int"),
                field("closes_on", "Date"),
                field("follow_up_at", "Datetime"),
                field("priority", "Check", default="0"),
                autoname="format:DEAL-{YYYY}-{###}",
            ),
        ],
    )
    return RecordsClient(engine)


def test_insert_names_converts_and_fills_defaults(crm):
    assert crm.insert("customer", {"customer_name": "Acme Traders"})["name"] == "CUST-0001"
    deal = crm.insert(
        "deal",
        {
            "title": "50 chairs",
            "customer": "CUST-0001",
            "amount": "12500.50",
            "probability": "70",
            "closes_on": "2026-10-01",
            "follow_up_at": "2026-09-25T10:30:00",  # no offset: taken as UTC
        },
        today=TODAY,
    )
    assert deal["name"] == "DEAL-2026-001"
    assert deal["amount"] == Decimal("12500.50")
    assert deal["probability"] == 70
    assert deal["closes_on"] == date(2026, 10, 1)
    assert deal["follow_up_at"] == datetime(2026, 9, 25, 10, 30, tzinfo=UTC)
    assert (deal["stage"], deal["priority"]) == ("Open", False)  # defaults
    assert deal["owner"] == deal["modified_by"] == "Administrator"
    assert deal["created_at"] == deal["modified_at"]
    assert "id" not in deal


def test_series_count_up_and_restart_each_year(crm):
    names = [
        crm.insert("deal", {"title": title}, today=date(year, 1, 1))["name"]
        for title, year in [("a", 2026), ("b", 2026), ("c", 2027)]
    ]
    assert names == ["DEAL-2026-001", "DEAL-2026-002", "DEAL-2027-001"]


def test_a_failed_insert_gives_its_number_back(crm):
    crm.insert("customer", {"customer_name": "A", "email": "a@acme.in"})
    with pytest.raises(DocumentConflict, match="email 'a@acme.in' already exists"):
        crm.insert("customer", {"customer_name": "B", "email": "a@acme.in"})
    assert crm.insert("customer", {"customer_name": "C"})["name"] == "CUST-0002"  # no gap


def test_every_problem_is_reported_at_once(crm):
    with pytest.raises(DocumentValidationError) as caught:
        crm.insert(
            "deal",
            {
                "title": "  ",
                "customer": "CUST-9999",
                "stage": "Maybe",
                "amount": "12.345",
                "probability": "lots",
                "colour": "red",
            },
        )
    errors = {e["field"]: e["message"] for e in caught.value.errors}
    assert errors.keys() == {"title", "customer", "stage", "amount", "probability", "colour"}
    assert errors["title"] == "is required"
    assert errors["customer"] == "Customer 'CUST-9999' doesn't exist"
    assert "'Open', 'Won' or 'Lost'" in errors["stage"]
    assert "decimal places" in errors["amount"]
    assert "valid integer" in errors["probability"]
    assert errors["colour"] == "isn't a field of Deal"


def test_other_naming_rules(engine):
    save_doctypes(
        engine,
        [
            doctype("tag", field("label")),  # autoname hash
            doctype("invoice", field("total", "Currency"), autoname="prompt"),
            doctype("company", field("gstin", length=15), autoname="field:gstin"),
        ],
    )
    records = RecordsClient(engine)
    assert re.fullmatch(r"[0-9a-f]{10}", records.insert("tag", {"label": "vip"})["name"])
    assert records.insert("invoice", {"name": "INV/24-25/001"})["name"] == "INV/24-25/001"
    assert records.insert("company", {"gstin": "27AAPFU0939F1ZV"})["name"] == "27AAPFU0939F1ZV"

    with pytest.raises(DocumentValidationError, match="named by hand"):
        records.insert("invoice", {"total": 1})
    with pytest.raises(DocumentValidationError, match="name comes from it"):
        records.insert("company", {})
    with pytest.raises(DocumentValidationError, match="set automatically"):
        records.insert("tag", {"name": "mine"})
    with pytest.raises(DocumentConflict, match="already exists"):
        records.insert("invoice", {"name": "INV/24-25/001"})


def test_update_changes_only_what_is_sent(crm):
    before = crm.insert("customer", {"customer_name": "Acme", "city": "Pune"})
    after = crm.update("customer", "CUST-0001", {"city": "Mumbai"})
    assert (after["customer_name"], after["city"]) == ("Acme", "Mumbai")
    assert after["modified_at"] > before["modified_at"]


def test_saving_the_record_back_unchanged_writes_nothing(crm):
    loaded = crm.insert("customer", {"customer_name": "Acme"})
    saved = crm.update("customer", "CUST-0001", loaded)  # echoing a read back is fine
    assert saved["modified_at"] == loaded["modified_at"]


def test_required_fields_cannot_be_cleared(crm):
    crm.insert("customer", {"customer_name": "Acme"})
    with pytest.raises(DocumentValidationError, match="customer_name: is required"):
        crm.update("customer", "CUST-0001", {"customer_name": ""})


def test_a_stale_edit_is_refused(crm):
    loaded = crm.insert("customer", {"customer_name": "Acme"})
    crm.update("customer", "CUST-0001", {"city": "Pune"})  # someone else saves first
    with pytest.raises(DocumentConflict, match="changed by someone else") as caught:
        crm.update(
            "customer",
            "CUST-0001",
            {"city": "Delhi", "modified_at": loaded["modified_at"].isoformat()},
        )
    assert caught.value.code == "stale"
    assert crm.get("customer", "CUST-0001")["city"] == "Pune"


def test_records_cannot_be_renamed_yet(crm):
    crm.insert("customer", {"customer_name": "Acme"})
    with pytest.raises(DocumentValidationError, match="name: can't be changed"):
        crm.update("customer", "CUST-0001", {"name": "CUST-9"})


def test_delete_archives_the_record(crm):
    crm.insert("customer", {"customer_name": "Acme"})
    crm.delete("customer", "CUST-0001")
    with pytest.raises(DocumentNotFound):
        crm.get("customer", "CUST-0001")
    assert rows(
        crm.engine,
        "SELECT value ->> 'customer_name' FROM sys_archive WHERE table_name = 'tab_customer'",
    ) == [("Acme",)]


def test_a_linked_record_cannot_be_deleted(crm):
    crm.insert("customer", {"customer_name": "Acme"})
    deal = crm.insert("deal", {"title": "Chairs", "customer": "CUST-0001"})
    with pytest.raises(DocumentConflict, match=deal["name"]):
        crm.delete("customer", "CUST-0001")
    crm.delete("deal", deal["name"])
    crm.delete("customer", "CUST-0001")  # nothing links to it any more


def test_unknown_doctypes_and_records(crm):
    with pytest.raises(DocumentNotFound, match="no DocType"):
        crm.get("invoice", "X")
    with pytest.raises(DocumentNotFound, match="not found"):
        crm.get("customer", "CUST-0404")


@pytest.fixture
def deals(crm):
    crm.insert("customer", {"customer_name": "Acme"})
    for title, stage, amount, closes_on, customer in [
        ("Chairs", "Open", 12000, "2026-10-01", "CUST-0001"),
        ("Desks", "Won", 45000, "2026-09-01", "CUST-0001"),
        ("Lamps", "Lost", 800, None, None),
        ("Shelves", "Open", 30000, "2026-11-15", "CUST-0001"),
    ]:
        crm.insert(
            "deal",
            {
                "title": title,
                "stage": stage,
                "amount": amount,
                "closes_on": closes_on,
                "customer": customer,
            },
            today=TODAY,
        )
    return crm


def titles(result):
    return [row["title"] for row in result["data"]]


def test_list_defaults_to_newest_first_with_list_view_fields(deals):
    result = deals.list("deal")
    assert result["total"] == 4
    assert titles(result) == ["Shelves", "Lamps", "Desks", "Chairs"]
    assert list(result["data"][0]) == ["name", "title", "stage"]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"stage": "Open"}, ["Chairs", "Shelves"]),
        ([["amount", ">=", "30000"]], ["Desks", "Shelves"]),
        ([["closes_on", "<", "2026-10-15"]], ["Chairs", "Desks"]),
        ([["title", "like", "%ES%"]], ["Desks", "Shelves"]),
        ([["stage", "in", ["Won", "Lost"]]], ["Desks", "Lamps"]),
        ([["customer", "is", "not set"]], ["Lamps"]),
        ([["stage", "!=", "Open"], ["amount", "<", 1000]], ["Lamps"]),
    ],
)
def test_list_filters(deals, filters, expected):
    result = deals.list("deal", filters=filters, order_by="title")
    assert titles(result) == expected
    assert result["total"] == len(expected)


def test_or_filters_match_any_and_combine_with_filters(deals):
    either = [["title", "like", "%chair%"], ["stage", "=", "Won"]]
    assert titles(deals.list("deal", or_filters=either, order_by="title")) == ["Chairs", "Desks"]
    narrowed = deals.list("deal", filters=[["amount", ">", 20000]], or_filters=either)
    assert titles(narrowed) == ["Desks"]


def test_list_fields_ordering_and_pages(deals):
    page = deals.list("deal", fields=["amount"], order_by="amount desc", limit=2, offset=1)
    assert page == {
        "data": [
            {"name": "DEAL-2026-004", "amount": Decimal("30000.00")},
            {"name": "DEAL-2026-001", "amount": Decimal("12000.00")},
        ],
        "total": 4,
    }
    assert list(deals.list("deal", fields=["*"])["data"][0]) == [
        "name",
        "title",
        "customer",
        "stage",
        "amount",
        "probability",
        "closes_on",
        "follow_up_at",
        "priority",
        "owner",
        "created_at",
        "modified_at",
        "modified_by",
    ]


@pytest.mark.parametrize(
    "request_",
    [
        {"fields": ["colour"]},
        {"filters": [["colour", "=", "red"]]},
        {"filters": [["amount", "~", 5]]},
        {"filters": [["amount", ">", "lots"]]},
        {"filters": [["stage", "in", "Open"]]},
        {"filters": ["stage"]},
        {"filters": "stage=Open"},
        {"order_by": "amount sideways"},
        {"limit": 0},
    ],
)
def test_bad_list_requests_are_rejected(deals, request_):
    with pytest.raises(QueryError):
        deals.list("deal", **request_)
