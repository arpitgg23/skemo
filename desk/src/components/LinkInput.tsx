import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useState } from "react";
import { Link } from "react-router";
import { api, type Doc, type Filter } from "../api";
import { useDebounced } from "../hooks";
import { docUrl, titleField, useDocType } from "../meta";

type Props = {
  id: string;
  target: string; // the DocType this field links to
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
};

/** A text box that suggests records of the target DocType, matching the ID or title. */
export function LinkInput({ id, target, value, onChange, ...aria }: Props) {
  const { doctype } = useDocType(target);
  const title = doctype ? titleField(doctype) : undefined;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
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

  function choose(doc: Doc) {
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
      <input
        id={id}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && options[active] ? optionId(active) : undefined}
        autoComplete="off"
        placeholder={`Search ${doctype?.label ?? target}…`}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        {...aria}
      />
      {value && !open && (
        <Link className="link-open" to={docUrl(target, value)} title={`Open ${value}`} tabIndex={-1}>
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
              <strong>{doc.name}</strong>
              {title && doc[title.fieldname] ? (
                <span className="muted"> · {String(doc[title.fieldname])}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
