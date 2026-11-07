"""Vercel's entry point: the whole Skemo API as one serverless function.

`vercel.json` rewrites every /api/... request here and Vercel runs `app` as an ASGI
application, so the routes are the same ones `skemo serve` exposes locally.

Two things differ from running on your own machine, and both come from the environment:

  SKEMO_SITE / SKEMO_SITE_DB   there is no writable disk here, so the site is named by
                               the environment instead of by a folder under sites/
  SKEMO_DB_URL                 a managed Postgres (Supabase, Neon) — use the *pooled*
                               connection string: a function may be started per request,
                               and direct connections run out long before traffic does

Seed the database before the first deploy; see deploy/seed_demo.py.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Installed apps are recorded relative to the working directory (see apps.portable_path),
# so "examples/crm" has to mean the same thing here as it did when it was installed.
os.chdir(ROOT)

from skemo.api import create_app  # noqa: E402  (the path above has to be set first)

app = create_app()
