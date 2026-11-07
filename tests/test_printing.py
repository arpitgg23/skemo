"""Printing a record: the default layout, an app's own template, and the values on paper."""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from factories import RecordsClient, doctype, field
from sqlalchemy import event, insert

from skemo.auth import create_user, load_user
from skemo.doctypes import save_doctypes
from skemo.documents import Records
from skemo.meta import DocField
from skemo.permissions import grant, install_roles
from skemo.printing import PrintError, day, money, number, render
from skemo.system_tables import sys_role

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


# A Link field stores the target's name, which is usually a code. Paper should say who
# that is. `titles` is what Records.link_titles works out, in the record's own shape.

ORDER_FORM = doctype(
    "order",
    field("customer", "Link", options="customer", reqd=True),
    DocField(fieldname="lines", fieldtype="Table", options="order_line"),
    label="Order",
)

ORDER = {
    "name": "SO-0001",
    "customer": "CUST-00002",
    "lines": [{"product": "P-1", "qty": 2}, {"product": "P-2", "qty": 1}],
}

ORDER_LINE = doctype(
    "order_line",
    field("product", "Link", options="product"),
    field("qty", "Int"),
    field("price", "Currency"),
    is_child=True,
)

# Keyed by target DocType and name, so a title follows its record wherever it appears.
ORDER_TITLES = {
    "customer": {"CUST-00002": "Asha Textiles"},
    "product": {"P-1": "Cotton fabric"},
}


def test_a_link_prints_who_it_points_at_not_the_code_it_stores():
    page = render(
        ORDER_FORM,
        ORDER,
        site="demo.localhost",
        titles=ORDER_TITLES,
        children={"order_line": ORDER_LINE},
    )
    assert "Asha Textiles" in page
    assert "CUST-00002" not in page
    # Grid cells too; a row pointing at something with no title keeps the stored code.
    assert "Cotton fabric" in page
    assert "P-2" in page


def test_a_grid_prints_its_columns_as_the_fields_they_are():
    page = render(
        ORDER_FORM,
        {**ORDER, "lines": [{"product": "P-1", "qty": 2, "price": Decimal("145000.5")}]},
        site="demo.localhost",
        titles=ORDER_TITLES,
        children={"order_line": ORDER_LINE},
    )
    assert "1,45,000.50" in page  # a Currency column reads as money, not as 145000.5


def test_a_link_with_no_title_still_prints_its_code():
    page = render(ORDER_FORM, ORDER, site="demo.localhost")
    assert "CUST-00002" in page and "P-1" in page


def test_a_template_gets_the_title_and_the_code():
    page = render(
        ORDER_FORM,
        ORDER,
        site="demo.localhost",
        titles=ORDER_TITLES,
        template=(
            "{{ show('customer') }} / {{ doc.customer }} / {{ titles['customer'][doc.customer] }}"
        ),
    )
    assert page == "Asha Textiles / CUST-00002 / Asha Textiles"


def test_show_falls_back_to_the_code_and_never_returns_a_grid():
    page = render(
        ORDER_FORM,
        ORDER,
        site="demo.localhost",
        template="[{{ show('customer') }}][{{ show('lines') }}]",
    )
    assert page == "[CUST-00002][]"


def test_a_title_cannot_inject_markup():
    page = render(
        ORDER_FORM,
        ORDER,
        site="demo.localhost",
        titles={"customer": {"CUST-00002": "<script>alert('x')</script>"}},
    )
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


# Working out those titles: one query per target DocType, and never a title the reader
# isn't allowed to see.


@pytest.fixture
def shop(engine):
    """A customer, three products, and an order whose grid points at them."""
    save_doctypes(
        engine,
        [
            doctype(
                "customer",
                field("customer_name", reqd=True),
                autoname="format:CUST-{###}",
            ),
            doctype("product", field("product_name", reqd=True), autoname="format:P-{#}"),
            doctype("slot", field("hour", "Int"), autoname="format:SLOT-{#}"),
            doctype(
                "order_line",
                field("product", "Link", options="product"),
                field("qty", "Int"),
                is_child=True,
            ),
            doctype(
                "order",
                field("customer", "Link", options="customer"),
                field("slot", "Link", options="slot"),
                DocField(fieldname="lines", fieldtype="Table", options="order_line"),
                autoname="format:SO-{###}",
            ),
        ],
    )
    records = RecordsClient(engine)
    customer = records.insert("customer", {"customer_name": "Asha Textiles"})
    cotton = records.insert("product", {"product_name": "Cotton fabric"})
    silk = records.insert("product", {"product_name": "Silk"})
    slot = records.insert("slot", {"hour": 9})
    order = records.insert(
        "order",
        {
            "customer": customer["name"],
            "slot": slot["name"],
            "lines": [
                {"product": cotton["name"], "qty": 2},
                {"product": silk["name"], "qty": 1},
                {"product": cotton["name"], "qty": 5},
            ],
        },
    )
    return records, order


@pytest.fixture
def as_user(engine):
    """as_user("asha@acme.in") -> a Records that checks permissions, as that user."""
    open_connections: list = []

    def make(login: str) -> Records:
        conn = engine.connect()
        transaction = conn.begin()
        open_connections.append((conn, transaction))
        user = load_user(conn, login)
        assert user is not None, f"no user {login}"
        return Records.load(conn, user=user, check_permissions=True)

    yield make
    for conn, transaction in open_connections:
        transaction.rollback()
        conn.close()


@contextmanager
def queries_hitting(engine, table: str):
    """Collects the SQL sent against one table while the block runs."""
    seen: list[str] = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        if f"tab_{table}" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", watch)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", watch)


def test_titles_come_back_in_the_shape_of_the_record(shop):
    records, order = shop
    titles = records.link_titles("order", order)

    assert titles["customer"] == {order["customer"]: "Asha Textiles"}
    # Grid rows too, keyed by value: the same product twice is still one entry, and
    # reordering rows on a form can't leave a title attached to the wrong one.
    assert titles["product"] == {"P-1": "Cotton fabric", "P-2": "Silk"}
    # A DocType with no text field has nothing to show but its name, so it's left out.
    assert "slot" not in titles


def test_one_query_per_target_however_many_rows_point_at_it(engine, shop):
    records, order = shop
    with queries_hitting(engine, "product") as product_queries:
        records.link_titles("order", order)
    assert len(product_queries) == 1


def test_a_link_to_a_record_that_has_gone_is_left_out(shop):
    records, _ = shop
    assert records.link_titles("order", {"customer": "CUST-404"}) == {}


def test_a_title_needs_permission_to_read_the_record_it_describes(engine, shop, as_user):
    records, order = shop
    with engine.begin() as conn:
        install_roles(conn)
        conn.execute(insert(sys_role), [{"name": "Sales"}])
        create_user(conn, "asha@acme.in", password="a good password", roles=["Sales"])
        grant(conn, "order", "Sales", ["read"])
        grant(conn, "product", "Sales", ["read"])
        # Deliberately no rule for customer.

    titles = as_user("asha@acme.in").link_titles("order", order)

    assert "customer" not in titles  # she may read the order, not who it's for
    assert titles["product"] == {"P-1": "Cotton fabric", "P-2": "Silk"}


def test_owner_only_means_titles_for_your_own_records_only(engine, shop, as_user):
    records, order = shop
    with engine.begin() as conn:
        install_roles(conn)
        conn.execute(insert(sys_role), [{"name": "Sales"}])
        create_user(conn, "asha@acme.in", password="a good password", roles=["Sales"])
        grant(conn, "order", "Sales", ["read"])
        grant(conn, "customer", "Sales", ["read"], if_owner=True)

    # The customer belongs to the Administrator, so to Asha it may as well not exist.
    assert "customer" not in as_user("asha@acme.in").link_titles("order", order)
