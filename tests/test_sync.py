"""The schema sync engine and its four guards, against a real Postgres."""

import pytest
from factories import columns, doctype, field, has_table, rows, run
from sqlalchemy import select

from dhancha.doctypes import DocTypeError, delete_doctype, save_doctype, save_doctypes
from dhancha.meta import DocField
from dhancha.store import load_doctypes
from dhancha.sync import SchemaSyncError
from dhancha.system_tables import sys_schema_log


def lead(*extra):
    return doctype("lead", field("lead_name", reqd=True), field("phone", length=20), *extra)


def stored_fields(engine, name):
    with engine.connect() as conn:
        return [(f.fieldname, f.fieldtype, f.unique) for f in load_doctypes(conn)[name].data_fields]


def test_saving_a_doctype_creates_its_table(engine):
    save_doctype(
        engine,
        doctype(
            "everything",
            DocField(fieldtype="Section Break", label="Main"),
            field("title"),
            field("notes", "Text"),
            field("qty", "Int"),
            field("weight", "Float"),
            field("amount", "Currency"),
            field("due", "Date"),
            DocField(fieldtype="Column Break"),
            field("starts_at", "Datetime"),
            field("done", "Check"),
            field("status", "Select", options="Open\nClosed"),
            field("parent_thing", "Link", options="everything"),
        ),
    )
    # Layout breaks make no columns; everything else maps to one typed column.
    assert columns(engine, "tab_everything") == {
        "id": "BIGINT",
        "name": "VARCHAR(140)",
        "owner": "VARCHAR(140)",
        "created_at": "TIMESTAMP WITH TIME ZONE",
        "modified_at": "TIMESTAMP WITH TIME ZONE",
        "modified_by": "VARCHAR(140)",
        "title": "VARCHAR(140)",
        "notes": "TEXT",
        "qty": "BIGINT",
        "weight": "DOUBLE PRECISION",
        "amount": "NUMERIC(18, 2)",
        "due": "DATE",
        "starts_at": "TIMESTAMP WITH TIME ZONE",
        "done": "BOOLEAN",
        "status": "VARCHAR(140)",
        "parent_thing": "VARCHAR(140)",
    }


def test_saving_the_same_definition_again_changes_nothing(engine):
    # Longest allowed names, so generated index/constraint names hit Postgres's limit.
    definition = doctype(
        "a" * 59,
        field("email", unique=True),
        field("phone", search_index=True),
        field("b" * 63, search_index=True),
    )
    assert save_doctype(engine, definition).changed
    assert save_doctype(engine, definition).statements == []


def test_adding_a_field_keeps_existing_rows(engine):
    save_doctype(engine, lead())
    run(engine, "INSERT INTO tab_lead (name, lead_name) VALUES ('L-1', 'Asha')")
    result = save_doctype(engine, lead(field("city")))
    assert result.statements == ["ALTER TABLE tab_lead ADD COLUMN city VARCHAR(140)"]
    assert rows(engine, "SELECT lead_name, city FROM tab_lead") == [("Asha", None)]


def test_removing_a_field_archives_its_values_then_drops_the_column(engine):
    save_doctype(engine, lead())
    run(
        engine,
        "INSERT INTO tab_lead (name, lead_name, phone) "
        "VALUES ('L-1', 'Asha', '98100'), ('L-2', 'Ravi', NULL)",
    )
    result = save_doctype(engine, doctype("lead", field("lead_name", reqd=True)))

    assert "phone" not in columns(engine, "tab_lead")
    assert result.archived_rows == 1
    # The archived value still points at its row, so it can be restored.
    assert rows(
        engine,
        "SELECT a.column_name, a.value #>> '{}', l.name "
        "FROM sys_archive a JOIN tab_lead l ON l.id = a.row_id",
    ) == [("phone", "98100", "L-1")]


def test_renaming_a_field_keeps_its_data(engine):
    save_doctype(engine, lead())
    run(engine, "INSERT INTO tab_lead (name, lead_name, phone) VALUES ('L-1', 'Asha', '98100')")
    renamed = doctype("lead", field("lead_name", reqd=True), field("mobile", length=20))

    result = save_doctype(engine, renamed, renames={"phone": "mobile"})

    assert len(result.statements) == 1 and "RENAME phone TO mobile" in result.statements[0]
    assert result.archived_rows == 0
    assert rows(engine, "SELECT mobile FROM tab_lead") == [("98100",)]


def test_a_type_change_converts_existing_values(engine):
    save_doctype(engine, doctype("item", field("qty")))
    run(engine, "INSERT INTO tab_item (name, qty) VALUES ('I-1', '42')")
    save_doctype(engine, doctype("item", field("qty", "Int")))
    assert columns(engine, "tab_item")["qty"] == "BIGINT"
    assert rows(engine, "SELECT qty FROM tab_item") == [(42,)]


def test_a_failed_change_rolls_back_everything(engine):
    save_doctype(engine, doctype("item", field("qty"), field("note")))
    run(engine, "INSERT INTO tab_item (name, qty) VALUES ('I-1', 'abc')")
    log_before = rows(engine, "SELECT count(*) FROM sys_schema_log")

    # The rename runs first and succeeds; converting 'abc' to a number then fails.
    with pytest.raises(SchemaSyncError, match="tab_item"):
        save_doctype(
            engine,
            doctype("item", field("qty", "Int"), field("remark")),
            renames={"note": "remark"},
        )

    live = columns(engine, "tab_item")
    assert live["qty"] == "VARCHAR(140)"
    assert "note" in live and "remark" not in live  # the rename was undone too
    assert stored_fields(engine, "item") == [("qty", "Data", False), ("note", "Data", False)]
    assert rows(engine, "SELECT count(*) FROM sys_schema_log") == log_before


def test_every_ddl_statement_is_logged(engine):
    result = save_doctype(engine, lead(field("email", unique=True)), actor="asha@example.com")
    with engine.connect() as conn:
        logged = conn.execute(select(sys_schema_log).order_by(sys_schema_log.c.id)).all()

    assert [r.statement for r in logged] == result.statements
    assert {(r.actor, r.table_name, r.batch_id) for r in logged} == {
        ("asha@example.com", "tab_lead", result.batch_id)
    }
    assert result.statements[0].startswith("CREATE TABLE tab_lead")


def test_deleting_a_doctype_archives_its_rows_and_drops_the_table(engine):
    save_doctype(engine, lead())
    run(engine, "INSERT INTO tab_lead (name, lead_name) VALUES ('L-1', 'Asha'), ('L-2', 'Ravi')")

    result = delete_doctype(engine, "lead")

    assert not has_table(engine, "tab_lead")
    assert result.archived_rows == 2
    assert rows(
        engine,
        "SELECT value ->> 'lead_name' FROM sys_archive "
        "WHERE table_name = 'tab_lead' AND column_name IS NULL ORDER BY row_id",
    ) == [("Asha",), ("Ravi",)]
    with engine.connect() as conn:
        assert "lead" not in load_doctypes(conn)


def test_a_doctype_others_link_to_cannot_be_deleted(engine):
    save_doctypes(
        engine,
        [
            doctype("customer", field("customer_name")),
            doctype("deal", field("customer", "Link", options="customer")),
        ],
    )
    with pytest.raises(DocTypeError, match="deal"):
        delete_doctype(engine, "customer")
    assert has_table(engine, "tab_customer")


def test_links_must_point_at_known_doctypes(engine):
    with pytest.raises(DocTypeError, match="unknown DocType"):
        save_doctype(engine, doctype("deal", field("customer", "Link", options="customer")))
    assert not has_table(engine, "tab_deal")


def test_doctypes_saved_together_can_link_to_each_other(engine):
    save_doctypes(
        engine,
        [
            doctype("deal", field("customer", "Link", options="customer")),
            doctype("customer", field("customer_name")),
        ],
    )
    assert has_table(engine, "tab_deal") and has_table(engine, "tab_customer")


def test_making_a_field_unique_fails_cleanly_on_duplicates(engine):
    save_doctype(engine, lead())
    run(
        engine,
        "INSERT INTO tab_lead (name, lead_name, phone) VALUES ('L-1', 'A', '1'), ('L-2', 'B', '1')",
    )
    with pytest.raises(SchemaSyncError, match="tab_lead"):
        save_doctype(
            engine,
            doctype("lead", field("lead_name", reqd=True), field("phone", length=20, unique=True)),
        )
    assert stored_fields(engine, "lead") == [("lead_name", "Data", False), ("phone", "Data", False)]


def test_removing_indexed_and_unique_fields(engine):
    save_doctype(engine, lead(field("city", search_index=True), field("email", unique=True)))
    save_doctype(engine, lead())
    assert {"city", "email"}.isdisjoint(columns(engine, "tab_lead"))


def test_sync_never_touches_tables_it_does_not_own(engine):
    run(engine, "CREATE TABLE legacy_orders (id int)")
    save_doctype(engine, lead())
    delete_doctype(engine, "lead")
    assert has_table(engine, "legacy_orders")


@pytest.mark.parametrize(
    ("new_fields", "renames", "message"),
    [
        (["lead_name", "mobile"], {"fax": "mobile"}, "isn't a field"),
        (["lead_name", "phone"], {"phone": "mobile"}, "still in the definition"),
        (["lead_name"], {"phone": "mobile"}, "missing from the definition"),
        (["phone"], {"lead_name": "phone"}, "that field exists"),
    ],
)
def test_invalid_renames_are_rejected(engine, new_fields, renames, message):
    save_doctype(engine, lead())
    with pytest.raises(DocTypeError, match=message):
        save_doctype(engine, doctype("lead", *map(field, new_fields)), renames=renames)


def test_a_new_doctype_has_nothing_to_rename(engine):
    with pytest.raises(DocTypeError, match="is new"):
        save_doctype(engine, lead(), renames={"phone": "mobile"})
