"""DocType validation. No database needed."""

import pytest
from factories import doctype, field
from pydantic import ValidationError

from skemo.meta import DocField, DocType


def test_labels_and_table_come_from_names():
    dt = doctype("sales_invoice", field("customer_name"), DocField(fieldtype="Section Break"))
    assert dt.label == "Sales Invoice"
    assert dt.fields[0].label == "Customer Name"
    assert dt.table_name == "tab_sales_invoice"
    assert [f.fieldname for f in dt.data_fields] == ["customer_name"]


@pytest.mark.parametrize("name", ["Lead", "1lead", "lead-item", "lead item", "x" * 60])
def test_bad_doctype_names_are_rejected(name):
    with pytest.raises(ValidationError):
        DocType(name=name)


@pytest.mark.parametrize(
    "fieldname", ["Phone", "2nd_phone", "phone-no", "id", "name", "owner", "x" * 64]
)
def test_bad_or_reserved_fieldnames_are_rejected(fieldname):
    with pytest.raises(ValidationError):
        field(fieldname)


@pytest.mark.parametrize(
    "props",
    [
        {"fieldtype": "Money"},  # unknown type
        {"fieldtype": "Select"},  # Select needs its choices
        {"fieldtype": "Select", "options": "  \n "},
        {"fieldtype": "Link"},  # Link needs a target DocType
        {"fieldtype": "Link", "options": "Not A DocType"},
        {"fieldtype": "Int", "length": 10},  # length is for Data only
        {"fieldtype": "Data", "length": 0},
        {"fieldtype": "Data", "colour": "red"},  # unknown property, e.g. a typo
    ],
)
def test_invalid_field_definitions_are_rejected(props):
    with pytest.raises(ValidationError):
        DocField(fieldname="f", **props)


def test_data_fields_need_a_fieldname_but_layout_fields_do_not():
    DocField(fieldtype="Column Break")
    with pytest.raises(ValidationError):
        DocField(fieldtype="Data")


@pytest.mark.parametrize(
    "autoname", ["hash", "prompt", "field:gstin", "format:INV-{YYYY}-{#####}", "format:{###}"]
)
def test_valid_autonames(autoname):
    doctype("invoice", field("gstin"), autoname=autoname)


@pytest.mark.parametrize(
    "autoname",
    [
        "random",
        "field:missing",
        "format:",
        "format:INV-{YYYY}",  # no counter
        "format:{##}-{##}",  # two counters
        "format:INV-{year}-{##}",
        "format:INV-{##}}",
    ],
)
def test_bad_autonames_are_rejected(autoname):
    with pytest.raises(ValidationError, match="autoname"):
        doctype("invoice", field("gstin"), autoname=autoname)


@pytest.mark.parametrize(
    ("fieldtype", "props", "default"),
    [
        ("Int", {}, "abc"),
        ("Select", {"options": "Open\nClosed"}, "Maybe"),
        ("Check", {}, "perhaps"),
        ("Date", {}, "tomorrow"),
        ("Data", {"length": 3}, "too long"),
    ],
)
def test_defaults_must_suit_their_field(fieldtype, props, default):
    with pytest.raises(ValidationError, match="default"):
        field("f", fieldtype, default=default, **props)


def test_today_and_now_are_allowed_as_defaults():
    field("on", "Date", default="Today")
    field("at", "Datetime", default="now")


def test_duplicate_fieldnames_are_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        doctype("lead", field("phone"), field("phone", "Int"))


def test_the_title_field_is_what_describes_a_record_in_words():
    listed = doctype(
        "deal",
        field("amount", "Currency", in_list_view=True),
        field("notes"),
        field("deal_title", in_list_view=True),
    )
    assert listed.title_field.fieldname == "deal_title"  # a Data field, shown in the list

    required = doctype("lead", field("notes"), field("lead_name", reqd=True))
    assert required.title_field.fieldname == "lead_name"

    first = doctype("lead", field("notes"), field("city"))
    assert first.title_field.fieldname == "notes"

    # Nothing to show: no plain text field, or the only one is hidden.
    assert doctype("slot", field("hour", "Int")).title_field is None
    assert doctype("token", field("secret", hidden=True)).title_field is None
