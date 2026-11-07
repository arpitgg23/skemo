"""Printing a record: the default layout, an app's own template, and the values on paper."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from factories import doctype, field

from skemo.meta import DocField
from skemo.printing import PrintError, day, money, number, render

INVOICE = doctype(
    "invoice",
    field("customer", reqd=True),
    field("invoice_date", "Date"),
    DocField(fieldtype="Section Break", label="Items"),
    DocField(fieldname="items", fieldtype="Table", options="invoice_item"),
    DocField(fieldtype="Section Break", label="Totals"),
    field("total", "Currency"),
    field("secret", read_only=True, hidden=True),
    label="Invoice",
)

RECORD = {
    "name": "INV-2026-0001",
    "customer": "Asha Textiles",
    "invoice_date": "2026-11-08",
    "items": [
        {
            "description": "Cotton fabric",
            "qty": 12.5,
            "rate": Decimal("240.00"),
            "amount": Decimal("3000.00"),
        },
        {
            "description": "Stitching",
            "qty": 3.0,
            "rate": Decimal("150.00"),
            "amount": Decimal("450.00"),
        },
    ],
    "total": Decimal("4071.00"),
    "secret": "not for paper",
    "owner": "Administrator",
    "created_at": datetime(2026, 11, 8, 10, 30, tzinfo=UTC),
}


def printed(**changes) -> str:
    return render(INVOICE, {**RECORD, **changes}, site="demo.localhost")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1234567.5, "12,34,567.50"),  # Indian grouping: lakhs, not thousands
        (Decimal("4071"), "4,071.00"),
        (999, "999.00"),
        (-450, "-450.00"),
        (0, "0.00"),
        (None, ""),
    ],
)
def test_money_reads_the_indian_way(value, expected):
    assert money(value) == expected


def test_numbers_and_dates():
    assert number(12.5) == "12.5"
    assert number(3.0) == "3"  # a whole quantity has no decimal point
    assert day("2026-11-08") == "8 Nov 2026"
    assert day(None) == ""


def test_the_default_layout_shows_the_form_as_it_stands():
    page = printed()
    assert "<title>Invoice INV-2026-0001</title>" in page
    assert "Asha Textiles" in page
    assert "8 Nov 2026" in page
    assert "4,071.00" in page
    # Grid rows become a table, in order.
    assert page.index("Cotton fabric") < page.index("Stitching")
    assert "<table>" in page
    # Section headings from the form, and hidden fields left off the paper.
    assert "Items" in page and "Totals" in page
    assert "not for paper" not in page


def test_empty_values_and_sections_are_left_out():
    page = printed(total=None, items=[], invoice_date=None)
    assert "Totals" not in page  # its only field was empty
    assert "<table>" not in page
    assert "Asha Textiles" in page


def test_a_customers_name_cannot_inject_markup():
    page = printed(customer="<script>alert('x')</script>")
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_an_apps_own_template_is_used():
    template = (
        "<h1>{{ doc.name }}</h1>"
        "{% for row in doc['items'] %}<p>{{ row.amount | money }}</p>{% endfor %}"
    )
    page = render(INVOICE, RECORD, site="demo.localhost", template=template)
    assert page == "<h1>INV-2026-0001</h1><p>3,000.00</p><p>450.00</p>"


def test_a_mistake_in_a_template_says_where():
    with pytest.raises(PrintError, match=r"crm/print_formats/invoice.html \(line 1\)"):
        render(
            INVOICE,
            RECORD,
            site="demo.localhost",
            template="{% for row in doc.items %}oops",
            source="crm/print_formats/invoice.html",
        )
    with pytest.raises(PrintError, match="nothing"):
        render(INVOICE, RECORD, site="demo.localhost", template="{{ doc.nothing }}")


def test_a_template_cannot_reach_into_the_process():
    with pytest.raises(PrintError):
        render(INVOICE, RECORD, site="demo.localhost", template="{{ doc.__class__.__mro__ }}")
