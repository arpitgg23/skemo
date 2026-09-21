"""The CLI, end to end, using the example CRM DocTypes."""

from pathlib import Path

from typer.testing import CliRunner

from dhancha.cli import app

EXAMPLES = Path(__file__).parent.parent / "examples" / "crm"


def test_cli_end_to_end(monkeypatch, settings, site_name):
    monkeypatch.setenv("DHANCHA_DB_URL", settings.db_url)
    monkeypatch.setenv("DHANCHA_SITES_DIR", str(settings.sites_dir))
    runner = CliRunner()

    def cli(*args: str) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    cli("new-site", site_name)
    try:
        out = cli("save-doctype", str(EXAMPLES))
        for table in ("tab_customer", "tab_deal", "tab_lead"):
            assert f"CREATE TABLE {table}" in out
        assert "Nothing to change" in cli("save-doctype", str(EXAMPLES))
        assert "Nothing to change" in cli("migrate")
        assert "CREATE TABLE tab_lead" in cli("schema-log")

        failed = runner.invoke(app, ["delete-doctype", "customer", "--yes"])
        assert failed.exit_code == 1 and "deal" in failed.output  # deal links to customer
    finally:
        cli("drop-site", site_name, "--yes")
