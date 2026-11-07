import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api, type DocType, type Filter } from "../api";
import { Banner, NotFound } from "../components/Feedback";
import { useDebounced, usePageTitle } from "../hooks";
import { choices, docUrl, listFields, titleField, useDocType } from "../meta";
import { allowed, useSession } from "../session";
import { formatValue, sentence, timeAgo } from "../values";

const PAGE_SIZE = 20;
const NUMERIC = new Set(["Int", "Float", "Currency"]);

export function ListView() {
  const { doctype: name } = useParams();
  const { doctype, isLoading } = useDocType(name);
  if (isLoading) return <p className="muted">Loading…</p>;
  if (!doctype) return <NotFound what={`DocType "${name}"`} />;
  return <DocList key={doctype.name} doctype={doctype} />;
}

/** One page of records. Page, sort, search and filters live in the URL, so reload and
 * the back button keep them. */
function DocList({ doctype }: { doctype: DocType }) {
  usePageTitle(doctype.label);
  const { user } = useSession();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Number(params.get("page")) || 1);
  const sort = params.get("sort") ?? "modified_at desc";
  const [sortField, sortDirection = "asc"] = sort.split(" ");
  const search = params.get("q") ?? "";
  const columns = listFields(doctype);
  const title = titleField(doctype);
  const filterable = columns.filter((f) => f.fieldtype === "Select" || f.fieldtype === "Check");

  const filters: Filter[] = filterable.flatMap((f): Filter[] => {
    const value = params.get(`f_${f.fieldname}`);
    if (value === null) return [];
    return [[f.fieldname, "=", f.fieldtype === "Check" ? value === "1" : value]];
  });
  const orFilters: Filter[] = [];
  if (search) {
    orFilters.push(["name", "like", `%${search}%`]);
    if (title) orFilters.push([title.fieldname, "like", `%${search}%`]);
  }

  const list = useQuery({
    queryKey: ["list", doctype.name, { page, sort, search, filters }],
    queryFn: () =>
      api.list(doctype.name, {
        fields: [...columns.map((f) => f.fieldname), "modified_at"],
        filters,
        orFilters,
        orderBy: sort,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  });

  function setParam(changes: Record<string, string | null>) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [key, value] of Object.entries(changes)) {
          if (value === null || value === "") next.delete(key);
          else next.set(key, value);
        }
        if (!("page" in changes)) next.delete("page"); // a new search, filter or sort starts at page 1
        return next;
      },
      { replace: true },
    );
  }

  const [searchText, setSearchText] = useState(search);
  const debouncedSearch = useDebounced(searchText.trim(), 300);
  useEffect(() => {
    if (debouncedSearch !== search) setParam({ q: debouncedSearch });
    // Only react to typing; `search` follows the URL.
  }, [debouncedSearch]);

  function toggleSort(fieldname: string) {
    const ascending = sortField === fieldname && sortDirection === "asc";
    setParam({ sort: ascending ? `${fieldname} desc` : fieldname });
  }

  const header = (label: string, fieldname: string, numeric = false) => {
    const sorted = sortField === fieldname;
    const descending = sortDirection === "desc";
    return (
      <th
        key={fieldname}
        className={numeric ? "num" : undefined}
        aria-sort={sorted ? (descending ? "descending" : "ascending") : undefined}
      >
        <button type="button" onClick={() => toggleSort(fieldname)}>
          {label}
          {sorted ? (descending ? " ↓" : " ↑") : ""}
        </button>
      </th>
    );
  };

  const data = list.data;
  const total = data?.total ?? 0;
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  const filtered = search !== "" || filters.length > 0;
  const newUrl = `/app/${doctype.name}/new`;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{doctype.label}</h1>
          {data && (
            <p className="muted">
              {total} {total === 1 ? "record" : "records"}
              {filtered ? (total === 1 ? " matches" : " match") : ""}
            </p>
          )}
        </div>
        <div className="actions">
          {user?.is_admin && (
            <Link className="muted" to={`/build/${doctype.name}`}>
              Design this form
            </Link>
          )}
          {allowed(doctype, "create") && (
            <Link className="button-link" to={newUrl}>
              New {doctype.label}
            </Link>
          )}
        </div>
      </div>

      <div className="toolbar">
        <input
          type="search"
          aria-label="Search"
          placeholder={`Search ID${title ? ` or ${title.label}` : ""}…`}
          value={searchText}
          onChange={(event) => setSearchText(event.target.value)}
        />
        {filterable.map((f) => (
          <select
            key={f.fieldname}
            aria-label={f.label}
            value={params.get(`f_${f.fieldname}`) ?? ""}
            onChange={(event) => setParam({ [`f_${f.fieldname}`]: event.target.value })}
          >
            <option value="">{`${f.label}: any`}</option>
            {f.fieldtype === "Check" ? (
              <>
                <option value="1">{`${f.label}: yes`}</option>
                <option value="0">{`${f.label}: no`}</option>
              </>
            ) : (
              choices(f).map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))
            )}
          </select>
        ))}
        {filtered && (
          <button
            type="button"
            onClick={() => {
              setSearchText("");
              setParams({}, { replace: true });
            }}
          >
            Clear
          </button>
        )}
      </div>

      {list.error && <Banner>{sentence(list.error.message)}</Banner>}
      {!data && !list.error && <p className="muted">Loading…</p>}
      {data && data.data.length === 0 && (
        <div className="panel empty">
          {filtered ? (
            <p>No {doctype.label} records match.</p>
          ) : (
            <>
              <p>No {doctype.label} records yet.</p>
              {allowed(doctype, "create") && (
                <Link className="button-link" to={newUrl}>
                  Create the first one
                </Link>
              )}
            </>
          )}
        </div>
      )}
      {data && data.data.length > 0 && (
        <div className="table-wrap">
          <table className="list-table">
            <thead>
              <tr>
                {header("ID", "name")}
                {columns.map((f) => header(f.label ?? f.fieldname, f.fieldname, NUMERIC.has(f.fieldtype)))}
                {header("Updated", "modified_at")}
              </tr>
            </thead>
            <tbody>
              {data.data.map((doc) => (
                <tr key={doc.name} onClick={() => navigate(docUrl(doctype.name, doc.name))}>
                  <td>
                    <Link to={docUrl(doctype.name, doc.name)} onClick={(event) => event.stopPropagation()}>
                      {doc.name}
                    </Link>
                  </td>
                  {columns.map((f) => (
                    <td key={f.fieldname} className={NUMERIC.has(f.fieldtype) ? "num" : undefined}>
                      {formatValue(f, doc[f.fieldname])}
                    </td>
                  ))}
                  <td className="muted">{timeAgo(doc.modified_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > PAGE_SIZE && (
        <div className="list-footer">
          <span className="muted">
            {first}–{last} of {total}
          </span>
          <div className="actions">
            <button type="button" disabled={page <= 1} onClick={() => setParam({ page: String(page - 1) })}>
              Previous
            </button>
            <button type="button" disabled={last >= total} onClick={() => setParam({ page: String(page + 1) })}>
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
