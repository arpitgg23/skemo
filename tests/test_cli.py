"""The CLI, end to end, using the example CRM app."""

from pathlib import Path

from typer.testing import CliRunner

from skemo.cli import app

CRM_APP = Path(__file__).parent.parent / "examples" / "crm"
TABLES = ("tab_customer", "tab_deal", "tab_lead")


def test_cli_end_to_end(monkeypatch, settings, site_name):
    monkeypatch.setenv("SKEMO_DB_URL", settings.db_url)
    monkeypatch.setenv("SKEMO_SITES_DIR", str(settings.sites_dir))
    runner = CliRunner()

    def cli(*args: str) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    cli("new-site", site_name, "--admin-password", "a good password")
    try:
        # Installing the app creates its tables; migrating again changes nothing.
        out = cli("install-app", str(CRM_APP))
        assert "Installed CRM (crm 0.1.0)" in out
        for table in TABLES:  # the app also ships invoice and invoice_item
            assert f"CREATE TABLE {table}" in out
        assert "Nothing to change" in cli("migrate")

        listed = cli("list-apps")
        assert "crm 0.1.0" in listed
        assert "DocTypes: customer, deal, invoice, invoice_item, lead" in listed

        assert "CREATE TABLE tab_lead" in cli("schema-log")

        # A DocType an app owns is deleted by removing its file, not from here.
        failed = runner.invoke(app, ["delete-doctype", "customer", "--yes"])
        assert failed.exit_code == 1 and "deal" in failed.output  # deal links to customer

        # A DocType made here can be exported into the app, ready to commit.
        one_off = settings.sites_dir / "note.json"
        one_off.write_text(
            '{"name": "note", "fields": [{"fieldname": "body", "fieldtype": "Text"}]}'
        )
        assert "CREATE TABLE tab_note" in cli("save-doctype", str(one_off))
        exported = cli("export-doctype", "note", "--to", str(CRM_APP))
        try:
            assert "now belongs to crm" in exported
            assert (CRM_APP / "doctypes" / "note.json").is_file()
            assert "invoice_item, lead, note" in cli("list-apps")
        finally:
            (CRM_APP / "doctypes" / "note.json").unlink(missing_ok=True)
    finally:
        cli("drop-site", site_name, "--yes")


def test_users_and_permissions_from_the_cli(monkeypatch, settings, site_name):
    monkeypatch.setenv("SKEMO_DB_URL", settings.db_url)
    monkeypatch.setenv("SKEMO_SITES_DIR", str(settings.sites_dir))
    runner = CliRunner()

    def cli(*args: str) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    cli("new-site", site_name, "--admin-password", "a good password")
    try:
        cli("install-app", str(CRM_APP))
        cli("grant", "lead", "--role", "Sales", "--actions", "read,create", "--if-owner")
        cli("add-user", "asha@acme.in", "--password", "asha good password", "--role", "Sales")

        listed = cli("permissions")
        assert "asha@acme.in: Sales" in listed
        assert "lead: Sales can read, create (own records only)" in listed

        failed = runner.invoke(app, ["add-user", "ravi@acme.in", "--role", "Typo"])
        assert failed.exit_code == 1 and "no role named 'Typo'" in failed.output
    finally:
        cli("drop-site", site_name, "--yes")
