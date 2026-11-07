# Dhancha

*ढांचा (dhaancha): Hindi for "framework, structure". Working name.*

A metadata-driven framework for building ERPs and CRMs, in the spirit of
[Frappe](https://frappe.io/framework). You describe a form (a **DocType**): its fields,
their types, and which ones are required. Dhancha creates and maintains the Postgres table
for it and serves a REST API for its records. Later milestones add the screens and a
drag-and-drop builder, all driven by the same metadata.

> **Status: M6 (apps and hooks) is done.** You can design a DocType by dragging
> fields, its Postgres table is created or altered when you save, and every screen and
> endpoint is behind a sign-in with per-DocType permissions. It hasn't been security
> reviewed or run anywhere but a laptop, so treat it as a work in progress.

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
dhancha new-site demo.localhost      # new database + an Administrator; prints their password
dhancha install-app examples/crm     # Customer, Deal, Lead -> tab_customer, tab_deal, tab_lead
dhancha schema-log                   # every DDL statement that ran
dhancha serve                        # API on http://127.0.0.1:8000
```

Then, in a second terminal:

```bash
cd desk
npm install
npm run dev                          # Desk on http://localhost:5173
```

## Apps: DocTypes in git, and your own Python

A DocType made in the builder lives in that one site's database. An **app** is a folder of
files instead, so it goes into git and ships to every site:

```
crm/
  app.json            {"name": "crm", "title": "CRM", "version": "0.1.0"}
  doctypes/
    deal.json         the DocType, the same JSON the builder saves
    deal.py           optional Python hooks
```

```bash
dhancha install-app ./crm    # add it to a site and create its tables
dhancha migrate              # re-read every installed app and apply the changes
dhancha list-apps            # what's installed, and which DocTypes came from where
```

Edit a file, commit it, and `migrate` applies it to every site that has the app — that's how
a change travels from your laptop to a customer's site. A DocType an app ships is **read-only
in the builder**, because its file is the truth; the builder says so and offers the file name.
Start the server with `DHANCHA_DEVELOPER_MODE=1` and the builder will edit it anyway and
**write the JSON back to the app folder** for you to commit. Deleting a DocType's file and
running `migrate` leaves its table alone: nothing is dropped behind your back.

`dhancha export-doctype note --to ./crm` moves a DocType you built in the browser into an app.

### Hooks

Next to a DocType's JSON, a module with a class adds the app's own logic:

```python
# crm/doctypes/deal.py
from dhancha.hooks import DocumentHooks, ValidationError

class Deal(DocumentHooks):
    def validate(self, doc):
        if doc["amount"] is not None and doc["amount"] < 0:
            doc.error("amount", "can't be negative")      # shown under that input
        if doc["stage"] == "Won" and doc["amount"] is None:
            raise ValidationError("A won deal needs an amount.", field="amount")

    def before_save(self, doc):
        if doc["stage"] == "Won":
            doc["probability"] = 100                      # computed, on create and update
```

`validate`, `before_save`, `after_save`, and the per-operation `before_insert`,
`after_insert`, `before_update`, `after_update`, `before_delete`, `after_delete`. All
optional.

- `doc` works like a dict of the record's values, plus `doc.name`, `doc.is_new`,
  `doc.previous` (what's stored, on an update), `doc.user`, `doc.changed("field")`, and
  `doc.conn` for a query of your own.
- Values a hook sets are checked against the field's type like anything else, so a hook
  can't write something the column won't take.
- Hooks run inside the save's transaction: raising undoes the whole thing, including any
  SQL the hook itself ran.
- What a save returns is the record **after** the hooks, so a value a hook computed shows
  up straight away.
- Editing a hook file takes effect on the next request — no restart.

## Signing in, roles and permissions

Everything except `/api/health` needs a session. Creating a site creates an **Administrator**
who is a **System Manager** — the role that may use the builder and reach every record.

```bash
dhancha add-user asha@acme.in --password "..." --full-name "Asha Patel" --role Sales
dhancha grant lead --role Sales --actions read,create,write --if-owner
dhancha permissions                  # who can do what on this site
```

- **A role is made by granting it something**: `grant` creates the role if it's new.
- **No rule means no access.** A DocType nobody has been granted is invisible to everyone
  but a System Manager, including in the sidebar and in `/api/meta/doctypes`.
- **`--if-owner`** limits a role to the records it created, so a sales rep sees only their
  own leads. Someone else's record reads as "not found", never as "forbidden", so the answer
  is the same whether or not it exists. Counts and filters obey the same limit.
- Several roles combine to the most generous: a second role without `--if-owner` lifts the
  limit.
- `dhancha set-roles <user> --role A --role B` replaces someone's roles;
  `dhancha set-password <user>` signs them out everywhere.

Passwords are hashed with scrypt from the standard library. Signing in sets an HttpOnly
cookie holding a random token; only the token's SHA-256 hash is stored, so a copy of the
database can't be used to sign in as anyone. Sessions last 14 days of inactivity. Set
`DHANCHA_COOKIE_SECURE=1` anywhere the site is served over HTTPS.

Not covered yet: no password reset or sign-up, no rate limiting on sign-in attempts, no
field-level permissions, and link autocomplete can confirm that a record of a linked
DocType exists even without read access to it.

## Builder

Open **http://localhost:5173/build**. No code, no restart: what you save becomes a real
Postgres table straight away.

- **App map:** every DocType, grouped by module, with an arrow for each Link field. Click
  one to edit it.
- **Form builder:** drag field types from the palette onto the form, or click to add. Drag
  cards to reorder them or move them between columns. Sections and columns are the same
  Section Break and Column Break the JSON uses.
- **Properties:** label, fieldname, type, choices, link target, default, required, unique,
  shown in list, and how records are named.
- **Preview:** the real form, built from the unsaved draft, using the same component the
  record screens use. Typing in it saves nothing.
- **Honest about the database:** after saving, the banner lists the exact DDL that ran
  (`ALTER TABLE tab_visit RENAME phone TO mobile`), or says the table already matched.
  Renaming a field renames its column and keeps the data — the properties panel says so
  before you save. A change the live data can't take (text that isn't a number) is refused,
  and nothing is applied.

Problems are caught in the browser first (duplicate fieldnames, a Select with no choices, a
Link with no target), and anything the server rejects is marked on the field it belongs to.

## Desk

The desk draws every screen from the metadata, so a DocType saved from the CLI appears in
the sidebar as soon as you switch back to the browser tab, with no restart or reload.

- **List:** the `in_list_view` columns, a search over ID and title, a dropdown filter for
  each Select or Check column, click-to-sort headers and paging. The page, search, filters
  and sort live in the URL, so reload and the back button keep them.
- **Form:** laid out by the DocType's Section Breaks (sections) and Column Breaks (columns),
  one input per field type. Link fields suggest records as you type, matching the ID or the
  title. New records start with their defaults (including `Today` and `Now`).
- **Saving:** required fields are checked in the browser first; the server's per-field errors
  appear under each input. Ctrl+S saves. If someone else saved the record after you opened
  it, you're told and offered a reload instead of overwriting their change. Leaving with
  unsaved changes asks first.

**Try a rename.** In `examples/crm/lead.json`, change `"phone"` to `"mobile"`, then run
`dhancha save-doctype examples/crm/lead.json --rename phone:mobile`. You get one
`RENAME COLUMN`, and the data is kept.

## DocType JSON

```json
{
  "name": "deal",
  "module": "CRM",
  "autoname": "format:DEAL-{YYYY}-{####}",
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
`hidden`, `default` (`Today` / `Now` work for dates), `description`. Every table also gets
`id`, `name` (unique), `owner`, `created_at`, `modified_at` and `modified_by`.

## Naming records

Every record has a unique, human-readable `name`, which is also what Link fields store. The
DocType's `autoname` decides how names are made:

| autoname | Example |
|---|---|
| `hash` (default) | `3f9c01b2e7` |
| `format:INV-{YYYY}-{#####}` | `INV-2026-00001`. The counter restarts each year and never skips a number, even when a save fails. |
| `field:gstin` | the record's GSTIN |
| `prompt` | typed in when the record is created, e.g. `INV/24-25/001` |

Placeholders: `{YYYY}` `{YY}` `{MM}` `{DD}`, plus one counter like `{####}`.

## Records API

Every DocType gets the same five endpoints (the same URL shape as Frappe):

| Method | URL | What it does |
|---|---|---|
| GET | `/api/resource/{doctype}` | List, with `fields`, `filters`, `order_by`, `limit`, `offset` |
| POST | `/api/resource/{doctype}` | Create |
| GET | `/api/resource/{doctype}/{name}` | Read |
| PUT | `/api/resource/{doctype}/{name}` | Update only the fields you send |
| DELETE | `/api/resource/{doctype}/{name}` | Delete; the record is archived first |

The metadata has its own endpoints, which is what the builder uses:

| Method | URL | What it does |
|---|---|---|
| GET | `/api/meta/doctypes` | The DocTypes you may read, with the columns their tables really have and what you may do with each |
| PUT | `/api/meta/doctype/{name}` | Create or update a DocType and sync its table; returns the DDL that ran. Body: `{"doctype": {…}, "renames": {"phone": "mobile"}, "create": false}`. System Managers only |
| DELETE | `/api/meta/doctype/{name}` | Delete a DocType and drop its table. System Managers only |
| GET | `/api/meta/schema-log` | Recent schema changes. System Managers only |

And for signing in:

| Method | URL | What it does |
|---|---|---|
| POST | `/api/auth/login` | `{"user": "...", "password": "..."}`; sets the session cookie |
| POST | `/api/auth/logout` | Ends the session and clears the cookie |
| GET | `/api/auth/me` | Who's signed in, their roles, and whether they're a System Manager |

Open **http://127.0.0.1:8000/docs** to try them in the browser. Sign in first: without a
session everything answers `401`, and `403` means signed in but not allowed.

- **Validation comes from the fields**: types, required fields, lengths, Select choices, and
  links that must point at real records. Every problem is reported at once:
  `422 {"detail": "Validation failed", "errors": [{"field": "customer", "message": "Customer 'CUST-9999' doesn't exist"}]}`
- **409 Conflict** covers a duplicate unique value, deleting a record that others link to, or
  an edit that clashes with someone else's. To get that last check, send back the
  `modified_at` you read. The response's `code` says which: `duplicate`, `linked` or `stale`.
- **Filters** are JSON: `{"stage": "Open"}` or
  `[["amount", ">", 1000], ["stage", "in", ["Open", "Won"]], ["email", "is", "set"]]`.
  Operators: `= != < <= > >= like` (ignores case, `%` wildcard) `not like in` `not in is`.
  All `filters` must hold; `or_filters` (same syntax) match when any one holds, e.g.
  searching by ID or name.
- `fields` is comma-separated or `*`; by default you get `name` plus the `in_list_view`
  fields. `order_by` looks like `amount desc`. Listing returns `{"data": [...], "total": n}`.

After upgrading Dhancha, run `dhancha migrate` on existing sites: it adds any new system
tables and columns, and re-reads the installed apps.

## Commands

| Command | What it does |
|---|---|
| `dhancha new-site NAME` | Create a site database with the system tables and an Administrator |
| `dhancha install-app PATH` / `list-apps` | Add an app to a site / show what's installed |
| `dhancha export-doctype NAME --to APP` | Move a DocType built in the browser into an app |
| `dhancha add-user LOGIN [--role R]` | Add a user |
| `dhancha set-password LOGIN` / `set-roles LOGIN --role R` | Change a password (signs them out) / replace roles |
| `dhancha grant DOCTYPE --role R --actions read,write [--if-owner]` | Say what a role may do |
| `dhancha revoke DOCTYPE ROLE` / `permissions` | Take access away / show who can do what |
| `dhancha list-sites` / `use NAME` | List sites / set the default |
| `dhancha save-doctype PATH... [--rename old:new]` | Create or update DocTypes from JSON files or folders |
| `dhancha delete-doctype NAME --yes` | Delete a DocType; its rows are archived, then the table is dropped |
| `dhancha migrate` | Bring a site up to date: system tables, the installed apps, then the tables |
| `dhancha schema-log` | Show recent schema changes |
| `dhancha serve` | Run the API: records under `/api/resource`, metadata under `/api/meta` |
| `dhancha drop-site NAME --yes` | Delete a site and its database |

## Tests

```bash
pytest
```

Tests run against the Postgres from `docker compose`, and each test gets its own
throwaway database. Set `DHANCHA_TEST_DB_URL` to use a different server.

The desk's layout and value-conversion logic has unit tests too: `cd desk && npm test`.

## Roadmap

| | Milestone | Status |
|---|---|---|
| M0 | Skeleton: API, desk, Postgres, CI, sites | done |
| M1 | Metadata, 10 field types, schema sync engine + 4 guards | done |
| M2 | Records REST API: validation from fields, naming series, link and edit-clash checks | done |
| M3 | Desk: list and form screens drawn from metadata | done |
| M4 | Drag-and-drop builder: app map, form builder, live preview | done |
| M5 | Login, roles, permissions per DocType | done |
| M6 | Developer layer: DocTypes as JSON in git, `migrate`, Python hooks | done |
| M7 | First client app: child tables, naming series, print formats | |

## License

MIT
