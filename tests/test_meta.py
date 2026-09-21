"""DocType validation. No database needed."""

import pytest
from factories import doctype, field
from pydantic import ValidationError

from dhancha.meta import DocField, DocType


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


def test_duplicate_fieldnames_are_rejected():
    with pytest.raises(ValidationError, match="duplicate"):
        doctype("lead", field("phone"), field("phone", "Int"))
