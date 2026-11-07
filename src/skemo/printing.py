"""Printing a record: a page you can hand to a customer.

Every DocType is printable without anyone writing a template — the default layout follows
the form's own sections, columns and grids. An app can ship its own instead, as
`print_formats/<doctype>.html`, written in Jinja:

    <h1>Invoice {{ doc.name }}</h1>
    {% for row in doc.items %}
      <tr><td>{{ row.description }}</td><td>{{ row.amount | money }}</td></tr>
    {% endfor %}

Templates are autoescaped, so a customer's name with an angle bracket in it stays text.

A Link field stores the target's name, which is usually a code. `{{ doc.customer }}` is
that code; `{{ show("customer") }}` is the customer's title ("Asha Textiles"), falling back
to the code when there isn't one. The default layout uses the title everywhere, so a
DocType with no template of its own reads properly without anyone doing anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .meta import DocField, DocType

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class PrintError(Exception):
    """The template couldn't be rendered. The message names the file and the line."""


def money(value: Any) -> str:
    """1234567.5 -> 12,34,567.50, the way an Indian invoice reads."""
    if value is None or value == "":
        return ""
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    sign, digits = ("-", str(-amount)) if amount < 0 else ("", str(amount))
    whole, _, paise = digits.partition(".")
    if len(whole) > 3:
        # Thousands, then every two digits: 1,23,45,678.
        head, last3 = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            head, pair = head[:-2], head[-2:]
            groups.insert(0, pair)
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, last3])
    return f"{sign}{whole}.{paise}"


def number(value: Any) -> str:
    """Whole numbers without a decimal point, decimals without trailing zeros."""
    if value is None or value == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def day(value: Any) -> str:
    """2026-11-08 -> 8 Nov 2026."""
    if not value:
        return ""
    when = date.fromisoformat(value) if isinstance(value, str) else value
    return f"{when.day} {MONTHS[when.month - 1]} {when.year}"


def moment(value: Any) -> str:
    if not value:
        return ""
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return f"{day(when.date())}, {when:%H:%M}"


def yesno(value: Any) -> str:
    return "Yes" if value else "No"


def format_value(field: DocField | None, value: Any) -> str:
    """One value as it should read on paper."""
    if value is None or value == "":
        return ""
    match field.fieldtype if field else "":
        case "Table":
            return ""  # a grid isn't one value; it prints as a table of its own
        case "Currency":
            return money(value)
        case "Float" | "Int":
            return number(value)
        case "Date":
            return day(value)
        case "Datetime":
            return moment(value)
        case "Check":
            return yesno(value)
        case _:
            return str(value)


def shown_value(field: DocField | None, value: Any, title: Any = None) -> str:
    """A value as it reads on paper: a Link shows the record's title, not its name."""
    return title if isinstance(title, str) and title else format_value(field, value)


def environment() -> Environment:
    """Jinja, sandboxed and autoescaped: an app's template can't reach into the process."""
    env = SandboxedEnvironment(autoescape=True, undefined=StrictUndefined, trim_blocks=True)
    env.filters |= {"money": money, "number": number, "day": day, "moment": moment, "yesno": yesno}
    return env


def render(
    doctype: DocType,
    doc: Mapping[str, Any],
    *,
    site: str,
    titles: Mapping[str, Any] | None = None,
    template: str | None = None,
    source: str = "the print format",
) -> str:
    """The printable page for one record, from the app's template or the default layout.

    `titles` is what `Records.link_titles` returns: the words behind each Link value, in
    the same shape as the record. Left out, every Link simply prints the name it stores.
    """
    env = environment()
    titles = titles or {}
    try:
        page = env.from_string(template if template is not None else DEFAULT_TEMPLATE)
        return page.render(
            doc=doc,
            doctype=doctype,
            site=site,
            titles=titles,
            sections=_sections(doctype, doc, titles),
            show=lambda fieldname: shown_value(
                _field(doctype, fieldname), doc.get(fieldname), titles.get(fieldname)
            ),
        )
    except TemplateError as e:
        line = f" (line {e.lineno})" if getattr(e, "lineno", None) else ""
        raise PrintError(f"{source}{line}: {e.message or e}") from e


def _field(doctype: DocType, fieldname: str) -> DocField | None:
    return next((f for f in doctype.fields if f.fieldname == fieldname), None)


def _sections(
    doctype: DocType, doc: Mapping[str, Any], titles: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The form's own layout, with each value formatted and empty parts left out."""
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for field in doctype.fields:
        if field.fieldtype == "Section Break":
            current = {"label": field.label or "", "entries": []}
            sections.append(current)
            continue
        if field.fieldtype == "Column Break" or field.hidden or not field.fieldname:
            continue
        if current is None:
            current = {"label": "", "entries": []}
            sections.append(current)
        if field.is_table:
            rows = doc.get(field.fieldname) or []
            if rows:
                current["entries"].append(_grid(field, rows, titles.get(field.fieldname)))
            continue
        shown = shown_value(field, doc.get(field.fieldname), titles.get(field.fieldname))
        if shown:
            current["entries"].append(
                {"kind": "value", "label": field.label or field.fieldname, "value": shown}
            )
    return [s for s in sections if s["entries"]]


def _grid(field: DocField, rows: list[Mapping[str, Any]], titles: Any = None) -> dict[str, Any]:
    """A child table as a printable table: columns from the first row's keys, in order.

    `titles` is one entry per row, as `Records.link_titles` builds it, so a row's Link
    cells read as the linked record's title.
    """
    columns = list(rows[0].keys())
    per_row = titles if isinstance(titles, list) else []
    return {
        "kind": "grid",
        "label": field.label or field.fieldname,
        "columns": [c.replace("_", " ").title() for c in columns],
        "rows": [
            [_cell(row.get(c), _row_titles(per_row, i).get(c)) for c in columns]
            for i, row in enumerate(rows)
        ],
    }


def _row_titles(per_row: list[Any], index: int) -> Mapping[str, Any]:
    found = per_row[index] if index < len(per_row) else None
    return found if isinstance(found, Mapping) else {}


def _cell(value: Any, title: Any = None) -> str:
    return shown_value(None, value, title) or _plain(value)


def _plain(value: Any) -> str:
    return "" if value is None else str(value)


# The layout every DocType gets for free. It prints as a plain, tidy page: the record's
# name, then each section of the form, with grids as tables.
DEFAULT_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ doctype.label }} {{ doc.name }}</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; padding: 32px; background: #f4f4f2; color: #16160f;
         font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .sheet { max-width: 760px; margin: 0 auto; padding: 40px; background: #fff;
           border: 1px solid #e0e0d8; border-radius: 4px; }
  header { display: flex; justify-content: space-between; align-items: baseline;
           gap: 16px; padding-bottom: 12px; border-bottom: 2px solid #16160f; }
  h1 { margin: 0; font-size: 20px; }
  .site { color: #6b6b62; font-size: 13px; }
  h2 { margin: 24px 0 8px; font-size: 13px; letter-spacing: .06em;
       text-transform: uppercase; color: #6b6b62; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 20px; margin: 0; }
  dt { color: #6b6b62; }
  dd { margin: 0; }
  table { width: 100%; margin: 8px 0 0; border-collapse: collapse; }
  th, td { padding: 7px 8px; border-bottom: 1px solid #e0e0d8; text-align: left;
           vertical-align: top; }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #6b6b62; }
  footer { margin-top: 28px; padding-top: 12px; border-top: 1px solid #e0e0d8;
           color: #6b6b62; font-size: 12px; }
  .print { position: fixed; top: 16px; right: 16px; padding: 8px 18px; border: 0;
           border-radius: 8px; background: #534ab7; color: #fff; font: inherit;
           cursor: pointer; }
  @media print {
    body { padding: 0; background: #fff; }
    .sheet { max-width: none; padding: 0; border: 0; }
    .print { display: none; }
  }
</style>
</head>
<body>
<button class="print" type="button" onclick="window.print()">Print</button>
<div class="sheet">
  <header>
    <h1>{{ doctype.label }} {{ doc.name }}</h1>
    <span class="site">{{ site }}</span>
  </header>
{% for section in sections %}
  {% if section.label %}<h2>{{ section.label }}</h2>{% endif %}
  {% for entry in section.entries %}
    {% if entry.kind == "grid" %}
  <table>
    <thead><tr>{% for column in entry.columns %}<th>{{ column }}</th>{% endfor %}</tr></thead>
    <tbody>
      {% for row in entry.rows %}
      <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
      {% endfor %}
    </tbody>
  </table>
    {% endif %}
  {% endfor %}
  {% set values = section.entries | selectattr("kind", "equalto", "value") | list %}
  {% if values %}
  <dl>
    {% for entry in values %}
    <dt>{{ entry.label }}</dt><dd>{{ entry.value }}</dd>
    {% endfor %}
  </dl>
  {% endif %}
{% endfor %}
  <footer>{{ doc.name }} · printed from {{ site }}</footer>
</div>
</body>
</html>
"""
