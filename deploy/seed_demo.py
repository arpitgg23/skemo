"""Fill (or empty and refill) the public demo on a managed Postgres.

The hosted demo is throwaway on purpose: anyone who opens it can design DocTypes, which
means anyone can also make a mess. This puts it back the way it was.

    # fill an empty database
    SKEMO_DB_URL="postgresql+psycopg://...pooler...supabase.com:6543/postgres" \
        python deploy/seed_demo.py

    # wipe Skemo's tables and start again
    SKEMO_DB_URL="..." python deploy/seed_demo.py --reset

Run it from the repository root: the CRM app's location is stored relative to the working
directory, so that the same path means something on Vercel too.

It never touches a table Skemo didn't make. --reset drops only tab_* and sys_*, so the
database can be shared with something else and still be safe.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from skemo.auth import create_user, set_password  # noqa: E402
from skemo.config import Settings  # noqa: E402
from skemo.doctypes import install_app  # noqa: E402
from skemo.documents import Records  # noqa: E402
from skemo.permissions import SYSTEM_MANAGER  # noqa: E402
from skemo.sites import Site, SiteError, get_engine, new_site  # noqa: E402

DEMO_USER = "demo@skemo.app"
DEMO_PASSWORD = "skemo-demo"  # shown on the sign-in page; the data is disposable
CRM = "examples/crm"


def drop_skemo_tables(engine: Engine) -> list[str]:
    """Remove every table Skemo owns, and nothing else."""
    with engine.connect() as conn:
        tables = [t for t in inspect(conn).get_table_names() if t.startswith(("tab_", "sys_"))]
    if tables:
        with engine.begin() as conn:
            names = ", ".join(f'"{t}"' for t in tables)
            conn.execute(text(f"DROP TABLE IF EXISTS {names} CASCADE"))
    return sorted(tables)


def sample_records(engine: Engine) -> None:
    """Enough of a business to be worth looking at: customers, a pipeline, an invoice."""
    with engine.begin() as conn:
        r = Records.load(conn)
        asha = r.insert(
            "customer",
            {
                "customer_name": "Asha Textiles",
                "phone": "+91 98200 11234",
                "email": "accounts@ashatextiles.in",
                "city": "Surat",
                "credit_limit": 200000,
            },
        )
        acme = r.insert(
            "customer",
            {
                "customer_name": "Acme Traders",
                "phone": "+91 99300 44556",
                "email": "buying@acmetraders.in",
                "city": "Pune",
                "credit_limit": 75000,
            },
        )
        for lead in (
            {"lead_name": "Kiran Furniture", "phone": "+91 90000 12345", "source": "Referral"},
            {"lead_name": "Bombay Drapes", "phone": "+91 90000 54321", "source": "Website"},
        ):
            r.insert("lead", lead)

        today = date.today()
        r.insert(
            "deal",
            {
                "title": "200 m cotton",
                "customer": asha["name"],
                "stage": "Won",
                "amount": 145000.50,
                "expected_close": str(today - timedelta(days=9)),
            },
        )
        r.insert(
            "deal",
            {
                "title": "Office chairs",
                "customer": acme["name"],
                "stage": "Negotiation",
                "amount": 62000,
                "expected_close": str(today + timedelta(days=21)),
            },
        )
        r.insert(
            "invoice",
            {
                "customer": asha["name"],
                "invoice_date": str(today),
                "status": "Draft",
                "gst_percent": 18,
                "items": [
                    {"description": "Cotton fabric (metres)", "qty": 12.5, "rate": 240},
                    {"description": "Stitching charges", "qty": 3, "rate": 150},
                ],
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="demo", help="site name (default: demo)")
    parser.add_argument(
        "--database",
        default="postgres",
        help="the database the host gave you (default: postgres)",
    )
    parser.add_argument(
        "--reset", action="store_true", help="drop Skemo's tables first and start again"
    )
    parser.add_argument("--admin-password", default=None, help="Administrator's password")
    args = parser.parse_args()

    settings = Settings.from_env()
    if "localhost" in settings.db_url or "127.0.0.1" in settings.db_url:
        print(f"Seeding {settings.db_url.rsplit('@', 1)[-1]} — that looks like your laptop.")
    # Single-site mode: the host has no disk, so nothing here depends on a sites folder.
    # new_site still writes one, so give it a throwaway that leaves the repo alone.
    settings = replace(
        settings,
        sites_dir=Path(tempfile.mkdtemp(prefix="skemo-seed-")),
        site=args.site,
        site_db=args.database,
    )
    site = Site(name=args.site, db_name=args.database)

    if args.reset:
        dropped = drop_skemo_tables(get_engine(site, settings))
        print(f"dropped {len(dropped)} tables" if dropped else "nothing to drop")

    try:
        new_site(
            args.site,
            settings,
            admin_password=args.admin_password or DEMO_PASSWORD,
            database=args.database,
        )
    except SiteError as e:
        print(f"! {e}\n  Run again with --reset to empty it first.", file=sys.stderr)
        return 1

    engine = get_engine(site, settings)
    app, result = install_app(engine, CRM)
    print(f"installed {app.title}: {len(result.sync.statements)} statements")

    with engine.begin() as conn:
        create_user(
            conn,
            DEMO_USER,
            password=DEMO_PASSWORD,
            full_name="Demo User",
            roles=[SYSTEM_MANAGER],
        )
        set_password(conn, "Administrator", args.admin_password or DEMO_PASSWORD)
    sample_records(engine)

    print(
        f"\nSeeded site {args.site!r} in database {args.database!r}.\n"
        f"  demo login : {DEMO_USER} / {DEMO_PASSWORD}\n"
        f"  set on Vercel: SKEMO_SITE={args.site}  SKEMO_SITE_DB={args.database}  "
        f"SKEMO_COOKIE_SECURE=1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
