"""Apps: DocTypes as files, installing them, and Python hooks running on save."""

import json
import shutil
from pathlib import Path

import pytest
from factories import RecordsClient, columns, has_table

from skemo.apps import AppError, app_of, installed_apps, load_app, load_installed, site_hooks
from skemo.doctypes import install_app, sync_site
from skemo.documents import DocumentValidationError
from skemo.store import app_owners, load_doctypes

EXAMPLE_CRM = Path(__file__).parent.parent / "examples" / "crm"

VISIT = {
    "name": "visit",
    "label": "Visit",
    "module": "Field Sales",
    "fields": [
        {"fieldname": "customer_name", "fieldtype": "Data", "reqd": True},
        {"fieldname": "amount", "fieldtype": "Currency"},
    ],
}

HOOKS = """
from skemo.hooks import DocumentHooks, ValidationError


class Visit(DocumentHooks):
    def validate(self, doc):
        if doc["amount"] is not None and doc["amount"] < 0:
            doc.error("amount", "can't be negative")

    def before_save(self, doc):
        doc["customer_name"] = (doc["customer_name"] or "").title()

    def after_insert(self, doc):
        doc.conn.exec_driver_sql(
            "UPDATE tab_visit SET customer_name = customer_name || %s WHERE name = %s",
            (" (new)", doc.name),
        )
"""


def write_app(
    folder: Path, *, name: str = "field", doctypes=(VISIT,), hooks: str | None = None
) -> Path:
    (folder / "doctypes").mkdir(parents=True, exist_ok=True)
    (folder / "app.json").write_text(
        json.dumps({"name": name, "title": name.title(), "version": "1.0.0"})
    )
    for doctype in doctypes:
        (folder / "doctypes" / f"{doctype['name']}.json").write_text(json.dumps(doctype))
    if hooks is not None:
        (folder / "doctypes" / f"{doctypes[0]['name']}.py").write_text(hooks)
    return folder


def test_installing_an_app_creates_its_tables(engine, tmp_path):
    app, result = install_app(engine, write_app(tmp_path / "field"))
    assert (app.name, app.version) == ("field", "1.0.0")
    assert any("CREATE TABLE tab_visit" in s for s in result.sync.statements)
    assert set(columns(engine, "tab_visit")) >= {"customer_name", "amount"}
    with engine.connect() as conn:
        assert app_owners(conn) == {"visit": "field"}  # standard, not builder-made
        assert [a.name for a in installed_apps(conn)] == ["field"]
        assert app_of(conn, "visit").path == app.path
        assert app_of(conn, "nothing") is None


def test_editing_the_file_and_migrating_applies_the_change(engine, tmp_path):
    folder = write_app(tmp_path / "field")
    install_app(engine, folder)

    changed = {**VISIT, "fields": [*VISIT["fields"], {"fieldname": "notes", "fieldtype": "Text"}]}
    (folder / "doctypes" / "visit.json").write_text(json.dumps(changed))
    result = sync_site(engine)

    assert result.apps == ["field"]
    assert "notes" in columns(engine, "tab_visit")
    with engine.connect() as conn:
        assert [f.fieldname for f in load_doctypes(conn)["visit"].data_fields] == [
            "customer_name",
            "amount",
            "notes",
        ]


def test_a_doctype_dropped_from_an_app_keeps_its_table(engine, tmp_path):
    folder = write_app(tmp_path / "field")
    install_app(engine, folder)
    (folder / "doctypes" / "visit.json").unlink()

    sync_site(engine)  # no surprise drops: the data stays until someone says otherwise

    assert has_table(engine, "tab_visit")


def test_an_app_that_moved_away_is_a_clear_error(engine, tmp_path):
    folder = write_app(tmp_path / "field")
    install_app(engine, folder)
    (folder / "app.json").unlink()
    with pytest.raises(AppError, match="isn't at"):
        sync_site(engine)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ({}, "no app.json"),
        ({"app.json": '{"name": "Field Sales"}'}, "must be lowercase"),
        ({"app.json": "not json"}, "app.json"),
        (
            {"app.json": '{"name": "field"}', "doctypes/visit.json": '{"name": "other"}'},
            "named 'visit'",
        ),
        (
            {
                "app.json": '{"name": "field"}',
                "doctypes/visit.json": '{"name": "visit", "fields": [{"fieldtype": "Nope"}]}',
            },
            "unknown fieldtype",
        ),
    ],
)
def test_a_broken_app_says_what_is_wrong(tmp_path, contents, message):
    folder = tmp_path / "broken"
    (folder / "doctypes").mkdir(parents=True)
    for name, text in contents.items():
        (folder / name).write_text(text)
    with pytest.raises(AppError, match=message):
        load_app(folder).doctypes() if (folder / "app.json").is_file() else load_app(folder)


def test_hooks_validate_change_values_and_react(engine, tmp_path):
    install_app(engine, write_app(tmp_path / "field", hooks=HOOKS))
    records = RecordsClient(engine)

    with pytest.raises(DocumentValidationError, match="amount: can't be negative"):
        records.insert("visit", {"customer_name": "acme traders", "amount": -5})

    # before_save tidied the name, then after_insert added to it. What comes back is the
    # record as it now stands, including what the after hook did.
    created = records.insert("visit", {"customer_name": "acme traders", "amount": 100})
    assert created["customer_name"] == "Acme Traders (new)"
    assert records.get("visit", created["name"])["customer_name"] == "Acme Traders (new)"

    updated = records.update("visit", created["name"], {"customer_name": "asha textiles"})
    assert updated["customer_name"] == "Asha Textiles"


def test_a_failed_hook_leaves_nothing_behind(engine, tmp_path):
    install_app(engine, write_app(tmp_path / "field", hooks=HOOKS))
    records = RecordsClient(engine)
    with pytest.raises(DocumentValidationError):
        records.insert("visit", {"customer_name": "acme", "amount": -1})
    assert records.list("visit")["total"] == 0


def test_hooks_are_reloaded_when_the_file_changes(engine, tmp_path):
    folder = write_app(tmp_path / "field", hooks=HOOKS)
    install_app(engine, folder)
    records = RecordsClient(engine)
    assert records.insert("visit", {"customer_name": "acme"})["customer_name"] == "Acme (new)"

    hooks_file = folder / "doctypes" / "visit.py"
    hooks_file.write_text(HOOKS.replace(".title()", ".upper()"))
    # Touch the future, so the change is visible even on a coarse clock.
    import os

    os.utime(hooks_file, (hooks_file.stat().st_atime, hooks_file.stat().st_mtime + 10))

    assert records.insert("visit", {"customer_name": "acme"})["customer_name"] == "ACME (new)"


def test_an_app_without_hooks_works(engine, tmp_path):
    install_app(engine, write_app(tmp_path / "field"))
    with engine.connect() as conn:
        assert site_hooks(conn) == {}
    assert (
        RecordsClient(engine).insert("visit", {"customer_name": "acme"})["customer_name"] == "acme"
    )


def test_the_example_crm_app_installs_and_its_hooks_run(engine):
    app, result = install_app(engine, EXAMPLE_CRM)
    assert app.name == "crm"
    for table in ("tab_customer", "tab_deal", "tab_lead"):
        assert has_table(engine, table)
    assert sorted(result.apps) == ["crm"]

    records = RecordsClient(engine)
    customer = records.insert("customer", {"customer_name": "Acme Traders"})
    with pytest.raises(DocumentValidationError, match="A won deal needs an amount"):
        records.insert("deal", {"title": "Chairs", "customer": customer["name"], "stage": "Won"})

    deal = records.insert(
        "deal", {"title": "Chairs", "customer": customer["name"], "stage": "Won", "amount": 5000}
    )
    assert deal["probability"] == 100  # before_save computed it
    assert "Won by Administrator." in records.get("deal", deal["name"])["notes"]  # after_save

    lost = records.update("deal", deal["name"], {"stage": "Lost"})
    assert lost["probability"] == 0


def test_an_apps_location_is_stored_so_it_survives_a_move(engine, tmp_path, monkeypatch):
    """An absolute path only means anything on the machine that wrote it.

    Checking the same repo out elsewhere — another laptop, a container, a serverless
    function — has to keep working, so a path inside the working directory is stored
    relative to it.
    """
    project = tmp_path / "project"
    (project / "apps").mkdir(parents=True)
    write_app(project / "apps" / "field")
    monkeypatch.chdir(project)

    install_app(engine, "apps/field")

    with engine.connect() as conn:
        [stored] = installed_apps(conn)
    assert stored.path == Path("apps/field")  # not C:/.../tmp/project/apps/field
    assert not stored.missing

    # The same checkout somewhere else still finds it.
    moved = tmp_path / "elsewhere"
    shutil.copytree(project, moved)
    monkeypatch.chdir(moved)
    with engine.connect() as conn:
        [stored] = installed_apps(conn)
        assert not stored.missing
        assert load_installed(conn)[0].name == "field"


def test_an_app_outside_the_working_directory_is_stored_absolute(engine, tmp_path, monkeypatch):
    outside = write_app(tmp_path / "outside" / "field")
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)

    install_app(engine, outside)

    with engine.connect() as conn:
        [stored] = installed_apps(conn)
    assert stored.path.is_absolute()
    assert not stored.missing
