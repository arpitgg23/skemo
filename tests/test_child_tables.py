"""Child tables: a grid of rows inside a record, like the lines on an invoice."""

from decimal import Decimal

import pytest
from factories import RecordsClient, columns, doctype, field, rows
from pydantic import ValidationError

from skemo.doctypes import DocTypeError, delete_doctype, save_doctypes
from skemo.documents import DocumentNotFound, DocumentValidationError
from skemo.meta import DocField, DocType


@pytest.fixture
def billing(engine):
    """An invoice with a grid of items, each linked to a product."""
    save_doctypes(
        engine,
        [
            doctype(
                "product",
                field("product_name", reqd=True),
                field("rate", "Currency"),
                autoname="format:PRODUCT-{#}",
            ),
            doctype(
                "invoice_item",
                field("product", "Link", options="product", reqd=True),
                field("qty", "Int", reqd=True),
                field("amount", "Currency"),
                is_child=True,
            ),
            doctype(
                "invoice",
                field("customer_name", reqd=True),
                DocField(fieldname="items", fieldtype="Table", options="invoice_item"),
                field("total", "Currency"),
                autoname="format:INV-{####}",
            ),
        ],
    )
    records = RecordsClient(engine)
    records.insert("product", {"product_name": "Chair", "rate": 1200})
    records.insert("product", {"product_name": "Desk", "rate": 4500})
    return records


def names(result):
    return [row["name"] for row in result["data"]]


def test_a_child_table_has_its_own_table_pointing_at_the_parent(engine, billing):
    live = columns(engine, "tab_invoice_item")
    assert live["parent"] == "VARCHAR(140)"
    assert live["parenttype"] == "VARCHAR(63)"
    assert live["parentfield"] == "VARCHAR(63)"
    assert live["idx"] == "INTEGER"
    assert {"product", "qty", "amount"} <= set(live)
    # A child row has no name, owner or modified_at of its own: it belongs to its record.
    assert {"name", "owner", "modified_at"}.isdisjoint(live)
    # The Table field itself is not a column on the invoice.
    assert "items" not in columns(engine, "tab_invoice")


def test_rows_are_saved_and_read_back_in_order(billing):
    invoice = billing.insert(
        "invoice",
        {
            "customer_name": "Acme",
            "items": [
                {"product": "PRODUCT-1", "qty": 2, "amount": 2400},
                {"product": "PRODUCT-2", "qty": 1, "amount": 4500},
            ],
        },
    )
    assert invoice["name"] == "INV-0001"
    assert invoice["items"] == [
        {"product": "PRODUCT-1", "qty": 2, "amount": Decimal("2400.00")},
        {"product": "PRODUCT-2", "qty": 1, "amount": Decimal("4500.00")},
    ]
    assert billing.get("invoice", "INV-0001")["items"] == invoice["items"]


def test_every_row_is_checked_like_any_other_value(billing):
    with pytest.raises(DocumentValidationError) as caught:
        billing.insert(
            "invoice",
            {
                "customer_name": "Acme",
                "items": [
                    {"product": "PRODUCT-1", "qty": 1},
                    {"product": "NOPE", "qty": "lots", "colour": "red"},
                ],
            },
        )
    messages = [e["message"] for e in caught.value.errors if e["field"] == "items"]
    assert any("row 2: colour isn't a field" in m for m in messages)
    assert any("row 2: qty" in m and "valid integer" in m for m in messages)
    assert any("row 2: Product 'NOPE' doesn't exist" in m for m in messages)
    assert billing.list("invoice")["total"] == 0  # nothing was written


def test_a_required_field_in_a_row(billing):
    with pytest.raises(DocumentValidationError, match="row 1: qty is required"):
        billing.insert("invoice", {"customer_name": "Acme", "items": [{"product": "PRODUCT-1"}]})


def test_updating_replaces_the_rows_given(billing):
    billing.insert(
        "invoice",
        {"customer_name": "Acme", "items": [{"product": "PRODUCT-1", "qty": 1, "amount": 1200}]},
    )
    updated = billing.update(
        "invoice",
        "INV-0001",
        {"items": [{"product": "PRODUCT-2", "qty": 3, "amount": 13500}]},
    )
    assert [row["product"] for row in updated["items"]] == ["PRODUCT-2"]
    assert rows(billing.engine, "SELECT count(*) FROM tab_invoice_item") == [(1,)]


def test_a_save_that_leaves_the_grid_out_keeps_it(billing):
    billing.insert(
        "invoice",
        {"customer_name": "Acme", "items": [{"product": "PRODUCT-1", "qty": 1, "amount": 1200}]},
    )
    updated = billing.update("invoice", "INV-0001", {"customer_name": "Acme Traders"})
    assert len(updated["items"]) == 1
    # And an empty list really does clear it.
    assert billing.update("invoice", "INV-0001", {"items": []})["items"] == []


def test_changing_only_the_grid_still_counts_as_a_change(billing):
    before = billing.insert("invoice", {"customer_name": "Acme", "items": []})
    after = billing.update("invoice", "INV-0001", {"items": [{"product": "PRODUCT-1", "qty": 1}]})
    assert len(after["items"]) == 1
    assert after["modified_at"] > before["modified_at"]
    # Saving the same rows back changes nothing.
    again = billing.update("invoice", "INV-0001", {"items": after["items"]})
    assert again["modified_at"] == after["modified_at"]


def test_deleting_a_record_archives_and_removes_its_rows(billing):
    billing.insert(
        "invoice",
        {"customer_name": "Acme", "items": [{"product": "PRODUCT-1", "qty": 2, "amount": 2400}]},
    )
    billing.delete("invoice", "INV-0001")

    assert rows(billing.engine, "SELECT count(*) FROM tab_invoice_item") == [(0,)]
    archived = rows(
        billing.engine,
        "SELECT table_name, value ->> 'qty' FROM sys_archive ORDER BY table_name",
    )
    assert archived == [("tab_invoice", None), ("tab_invoice_item", "2")]
    # Both went to the archive together, under one batch.
    assert rows(billing.engine, "SELECT count(DISTINCT batch_id) FROM sys_archive") == [(1,)]


def test_a_child_doctype_cannot_be_used_on_its_own(billing):
    for call in (
        lambda: billing.list("invoice_item"),
        lambda: billing.insert("invoice_item", {"product": "PRODUCT-1", "qty": 1}),
        lambda: billing.get("invoice_item", "anything"),
    ):
        with pytest.raises(DocumentNotFound, match="table inside Invoice"):
            call()


def test_a_child_doctype_in_use_cannot_be_deleted(engine, billing):
    with pytest.raises(DocTypeError, match="can't delete 'invoice_item': invoice use"):
        delete_doctype(engine, "invoice_item")


def test_what_a_table_field_may_point_at(engine, billing):
    with pytest.raises(DocTypeError, match="must be a child DocType"):
        save_doctypes(
            engine,
            [doctype("quote", DocField(fieldname="rows", fieldtype="Table", options="product"))],
        )
    with pytest.raises(DocTypeError, match="holds unknown DocType"):
        save_doctypes(
            engine,
            [doctype("quote", DocField(fieldname="rows", fieldtype="Table", options="nope"))],
        )
    with pytest.raises(DocTypeError, match="rows have no name to link to"):
        save_doctypes(engine, [doctype("quote", field("line", "Link", options="invoice_item"))])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"autoname": "format:ROW-{###}"}, "can't have a naming rule"),
        (
            {"fields": [DocField(fieldname="more", fieldtype="Table", options="other")]},
            "can't have the Table field",
        ),
    ],
)
def test_what_a_child_doctype_cannot_have(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        DocType(name="line", is_child=True, **kwargs)


def test_a_table_field_needs_a_child_doctype_in_options():
    with pytest.raises(ValidationError, match="must name a child DocType"):
        DocField(fieldname="items", fieldtype="Table")
    with pytest.raises(ValidationError, match="can't be unique"):
        DocField(fieldname="items", fieldtype="Table", options="invoice_item", unique=True)


HOOK = """
from skemo.hooks import DocumentHooks


class Invoice(DocumentHooks):
    def before_save(self, doc):
        doc["total"] = sum(row["amount"] or 0 for row in doc["items"])
"""


def test_a_hook_can_add_up_the_rows(engine, billing, tmp_path):
    """The reason child tables exist: a total computed from the lines."""
    from skemo.doctypes import install_app

    folder = tmp_path / "billing"
    (folder / "doctypes").mkdir(parents=True)
    (folder / "app.json").write_text('{"name": "billing"}')
    # The app ships the invoice, so its hook file is picked up.
    with engine.connect() as conn:
        from skemo.store import load_doctypes

        invoice = load_doctypes(conn)["invoice"]
    (folder / "doctypes" / "invoice.json").write_text(
        invoice.model_dump_json(exclude_defaults=True)
    )
    (folder / "doctypes" / "invoice.py").write_text(HOOK)
    install_app(engine, folder)

    records = RecordsClient(engine)
    created = records.insert(
        "invoice",
        {
            "customer_name": "Acme",
            "items": [
                {"product": "PRODUCT-1", "qty": 2, "amount": 2400},
                {"product": "PRODUCT-2", "qty": 1, "amount": 4500},
            ],
        },
    )
    assert created["total"] == Decimal("6900.00")

    smaller = records.update("invoice", created["name"], {"items": [created["items"][0]]})
    assert smaller["total"] == Decimal("2400.00")
