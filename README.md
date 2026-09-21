# Dhancha

*ढांचा (dhaancha): Hindi for "framework, structure". Working name.*

A metadata-driven framework for building ERPs and CRMs, in the spirit of
[Frappe](https://frappe.io/framework). You describe a form (a **DocType**): its fields,
their types, and which ones are required. Dhancha creates and maintains the Postgres table
for it. Later milestones add the REST API, the screens and a drag-and-drop builder, all
driven by the same metadata.

> **Status: M1 (auto-tables) is done.** There's no record API, builder or login yet, so
> don't expose the server to the internet.

## How it works

```
 Business user (drag-and-drop builder, M4)     Developer (JSON in git + Python hooks, M6)
                     \                                  /
                      v                                v
              Metadata: Module -> DocType -> Field   (sys_doctype)
                 |                    |                     |
                 v                    v                     v
        Schema sync engine      REST API (M2)        Desk UI (M3)
                 |                    |
                 v                    v
        PostgreSQL, one database per site: tab_lead, tab_deal, tab_customer ...
```

Saving a DocType runs **one transaction** that validates it, stores the metadata and syncs
the tables. Alembic's autogenerate compares the tables the metadata wants with the live
database, and the sync engine wraps that diff in four guards:

1. **One transaction per save.** Postgres DDL is transactional: if any step fails (say,
   converting the text `abc` to a number), the whole save is undone. Nothing is half-applied.
2. **Explicit renames.** A renamed field becomes `RENAME COLUMN`. The diff alone can't tell
   a rename from a drop + add, and would lose the column's data.
3. **Schema log.** Every DDL statement is recorded in `sys_schema_log`: who, when, exact SQL.
4. **Archive before drop.** Before a column or table is dropped, its data is copied to
   `sys_archive`, so a mis-click can be undone.

Type changes convert existing data automatically (`ALTER ... USING col::bigint`). "Required"
is checked by the API rather than with `NOT NULL`, so toggling it never needs a table change.

## Quick start

Needs Python 3.11+, Node 20+ and Docker.

```bash
docker compose up -d                 # Postgres 16 on localhost:5433
uv venv && uv pip install -e ".[dev]"
```

(Without uv: `python -m venv .venv`, then `pip install -e ".[dev]"` inside it.)
Activate the venv with `.venv\Scripts\activate` on Windows or `source .venv/bin/activate` elsewhere.

```bash
dhancha new-site demo.localhost      # new database site_demo_localhost, set as the default
dhancha save-doctype examples/crm    # Customer, Deal, Lead -> tab_customer, tab_deal, tab_lead
dhancha schema-log                   # every DDL statement that ran
dhancha serve                        # API on http://127.0.0.1:8000
```

Then, in a second terminal:

```bash
cd desk
npm install
npm run dev                          # Desk on http://localhost:5173
```

**Try a rename.** In `examples/crm/lead.json`, change `"phone"` to `"mobile"`, then run
`dhancha save-doctype examples/crm/lead.json --rename phone:mobile`. You get one
`RENAME COLUMN`, and the data is kept.

## DocType JSON

```json
{
  "name": "deal",
  "module": "CRM",
  "fields": [
    { "fieldname": "title", "fieldtype": "Data", "reqd": true },
    { "fieldname": "customer", "fieldtype": "Link", "options": "customer" },
    { "fieldtype": "Column Break" },
    { "fieldname": "amount", "fieldtype": "Currency" }
  ]
}
```

| Field type | Postgres column | Notes |
|---|---|---|
| Data | `VARCHAR(140)` | `length` sets the size (1–1000) |
| Text | `TEXT` | |
| Int | `BIGINT` | |
| Float | `DOUBLE PRECISION` | |
| Currency | `NUMERIC(18, 2)` | |
| Date / Datetime | `DATE` / `TIMESTAMPTZ` | |
| Check | `BOOLEAN` | |
| Select | `VARCHAR(140)` | `options`: one choice per line |
| Link | `VARCHAR(140)` | `options`: the DocType it points to; stores that record's `name` |
| Section Break / Column Break | none | layout only |

Field properties: `label`, `reqd`, `unique`, `search_index`, `in_list_view`, `read_only`,
`hidden`, `default`, `description`. Every table also gets `id`, `name` (unique),
`owner`, `created_at`, `modified_at` and `modified_by`.

## Commands

| Command | What it does |
|---|---|
| `dhancha new-site NAME` | Create a site database with the system tables |
| `dhancha list-sites` / `use NAME` | List sites / set the default |
| `dhancha save-doctype PATH... [--rename old:new]` | Create or update DocTypes from JSON files or folders |
| `dhancha delete-doctype NAME --yes` | Delete a DocType; its rows are archived, then the table is dropped |
| `dhancha migrate` | Re-sync every table from the stored metadata |
| `dhancha schema-log` | Show recent schema changes |
| `dhancha serve` | Run the API (`/api/health`, `/api/meta/doctypes`, `/api/meta/schema-log`) |
| `dhancha drop-site NAME --yes` | Delete a site and its database |

## Tests

```bash
pytest
```

Tests run against the Postgres from `docker compose`, and each test gets its own
throwaway database. Set `DHANCHA_TEST_DB_URL` to use a different server.

## Roadmap

| | Milestone | Status |
|---|---|---|
| M0 | Skeleton: API, desk, Postgres, CI, sites | done |
| M1 | Metadata, 10 field types, schema sync engine + 4 guards | done |
| M2 | Generic REST API with validation from fields | |
| M3 | Desk: list and form screens drawn from metadata | |
| M4 | Drag-and-drop builder: app map, form builder, live preview | |
| M5 | Login, roles, permissions per DocType | |
| M6 | Developer layer: DocTypes as JSON in git, `migrate`, Python hooks | |
| M7 | First client app: child tables, naming series, print formats | |

## License

MIT
