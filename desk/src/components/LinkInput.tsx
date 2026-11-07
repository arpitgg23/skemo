import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useState } from "react";
import { Link } from "react-router";
import { api, type Doc, type Filter, type LinkTitles } from "../api";
import { useDebounced } from "../hooks";
import { docUrl, titleField, useDocType } from "../meta";

type Props = {
  id: string;
  target: string; // the DocType this field links to
  value: string;
  onChange: (value: string) => void;
  /** Titles for every Link value on this record, by target DocType and name. */
  titles?: LinkTitles;
  /** A grid cell: no room for the code underneath. */
  compact?: boolean;
  disabled?: boolean;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
};

/** A text box that suggests records of the target DocType, matching the ID or title.
 *
 * A Link stores a code like CUST-00002, so the box shows who that is and keeps the code
 * underneath. Typing searches; what's typed is shown as typed, so nothing is ever hidden
 * from the person editing it. */
export function LinkInput({ id, target, value, onChange, titles, compact, ...aria }: Props) {
  const { doctype } = useDocType(target);
  const title = doctype ? titleField(doctype) : undefined;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  // What they're typing, while they're typing it. null means the box shows the record.
  const [draft, setDraft] = useState<string | null>(null);
  // A record picked just now, which the titles we were handed don't know about yet.
  const [picked, setPicked] = useState<Doc | null>(null);
  const search = useDebounced(value.trim(), 250);

  const results = useQuery({
    queryKey: ["link-search", target, search],
    queryFn: () => {
      const orFilters: Filter[] = [["name", "like", `%${search}%`]];
      if (title) orFilters.push([title.fieldname, "like", `%${search}%`]);
      return api.list(target, {
        fields: title ? [title.fieldname] : [],
        orFilters: search ? orFilters : undefined,
        limit: 8,
      });
    },
    enabled: open && Boolean(doctype),
  });
  const options = results.data?.data ?? [];

  const named = (doc: Doc) => (title ? String(doc[title.fieldname] ?? "") : "");
  const known = picked?.name === value ? named(picked) : (titles?.[target]?.[value] ?? "");
  const shown = draft ?? (known || value);

  function choose(doc: Doc) {
    setPicked(doc);
    setDraft(null);
    onChange(doc.name);
    setOpen(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, options.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter" && open && options[active]) {
      event.preventDefault();
      choose(options[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  }

  const listId = `${id}-options`;
  const optionId = (i: number) => `${listId}-${i}`;
  return (
    <div className="link-input">
      {/* The arrow and the list hang off the box, so the code underneath can't move them. */}
      <div className="link-box">
        <input
          id={id}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={open && options[active] ? optionId(active) : undefined}
          autoComplete="off"
          placeholder={`Search ${doctype?.label ?? target}…`}
          value={shown}
          onChange={(event) => {
            setDraft(event.target.value);
            onChange(event.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={(event) => {
            setOpen(true);
            event.target.select(); // typing replaces the record, rather than editing its name
          }}
          onBlur={() => {
            setOpen(false);
            setDraft(null); // back to showing the record, whatever was half-typed
          }}
          onKeyDown={onKeyDown}
          {...aria}
        />
        {value && !open && (
          <Link
            className="link-open"
            to={docUrl(target, value)}
            title={`Open ${value}`}
            tabIndex={-1}
          >
            ↗
          </Link>
        )}
        {open && (
          <ul id={listId} role="listbox" className="link-options">
            {results.isFetching && options.length === 0 && <li className="muted">Searching…</li>}
            {!results.isFetching && options.length === 0 && <li className="muted">No matches</li>}
            {options.map((doc, i) => (
              <li
                key={doc.name}
                id={optionId(i)}
                role="option"
                aria-selected={i === active}
                onMouseDown={(event) => {
                  event.preventDefault(); // keep focus, so the blur doesn't close the list first
                  choose(doc);
                }}
                onMouseEnter={() => setActive(i)}
              >
                <strong>{named(doc) || doc.name}</strong>
                {named(doc) && <span className="muted"> · {doc.name}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
      {/* The code the record is stored and linked by, now the box is showing its name. */}
      {known && !compact && draft === null && <p className="link-id">{value}</p>}
    </div>
  );
}
