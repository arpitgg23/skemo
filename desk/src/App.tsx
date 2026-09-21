import { useCallback, useEffect, useState } from "react";
import { api, type DocType, type LogEntry } from "./api";

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; site: string; version: string; doctypes: DocType[]; log: LogEntry[] };

export default function App() {
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(async () => {
    setState({ status: "loading" });
    try {
      const [health, meta, log] = await Promise.all([
        api.health(),
        api.doctypes(),
        api.schemaLog(15),
      ]);
      setState({
        status: "ready",
        site: meta.site,
        version: health.version,
        doctypes: meta.doctypes,
        log: log.entries,
      });
    } catch (error) {
      setState({ status: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          Dhancha <span className="muted">desk</span>
        </div>
        {state.status === "ready" && (
          <div className="muted">
            site <code>{state.site}</code> · v{state.version}
          </div>
        )}
        <button onClick={() => void load()} disabled={state.status === "loading"}>
          Refresh
        </button>
      </header>

      {state.status === "loading" && <p className="muted">Loading…</p>}
      {state.status === "error" && (
        <section className="panel error">
          <h2>Can't reach the API</h2>
          <p>{state.message}</p>
          <p className="muted">
            Is it running? Start it with <code>dhancha serve</code>, and make sure a site exists (
            <code>dhancha new-site demo.localhost</code>).
          </p>
        </section>
      )}
      {state.status === "ready" && (
        <>
          <AppStructure doctypes={state.doctypes} />
          <SchemaLog entries={state.log} />
        </>
      )}
    </div>
  );
}

function AppStructure({ doctypes }: { doctypes: DocType[] }) {
  if (doctypes.length === 0) {
    return (
      <section className="panel">
        <h2>No DocTypes yet</h2>
        <p>Create some from JSON, then refresh:</p>
        <pre>dhancha save-doctype examples/crm</pre>
      </section>
    );
  }
  const modules = groupBy(doctypes, (dt) => dt.module);
  return (
    <section>
      <h2>App structure</h2>
      <p className="muted">
        Every form below is a DocType. The right-hand column shows the column that really exists
        in Postgres.
      </p>
      <div className="modules">
        {Object.entries(modules).map(([module, items]) => (
          <div key={module}>
            <h3 className="module-name">{module}</h3>
            {items.map((dt) => (
              <DocTypeCard key={dt.name} doctype={dt} />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

function DocTypeCard({ doctype }: { doctype: DocType }) {
  const columnType = new Map(doctype.columns.map((c) => [c.name, c.type]));
  return (
    <article className="card">
      <header>
        <strong>{doctype.label}</strong>
        <code className="muted">{doctype.table}</code>
      </header>
      <ul>
        {doctype.fields.map((f, i) => {
          if (f.fieldtype === "Column Break") return null;
          if (f.fieldtype === "Section Break") {
            return (
              <li key={i} className="section">
                {f.label || "Section"}
              </li>
            );
          }
          return (
            <li key={f.fieldname ?? i}>
              <span>{f.label}</span>
              {f.reqd && <span className="badge">required</span>}
              {f.unique && <span className="badge">unique</span>}
              <span className="type">{f.fieldtype === "Link" ? `→ ${f.options}` : f.fieldtype}</span>
              <code className="column">{columnType.get(f.fieldname ?? "") ?? "missing"}</code>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

function SchemaLog({ entries }: { entries: LogEntry[] }) {
  return (
    <section>
      <h2>Recent schema changes</h2>
      {entries.length === 0 ? (
        <p className="muted">None yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="log">
            <thead>
              <tr>
                <th>When</th>
                <th>Table</th>
                <th>Statement</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={i}>
                  <td className="nowrap">{new Date(e.created_at).toLocaleString()}</td>
                  <td>
                    <code>{e.table}</code>
                  </td>
                  <td>
                    <code className="sql">{e.statement.replace(/\s+/g, " ")}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function groupBy<T>(items: T[], key: (item: T) => string): Record<string, T[]> {
  const groups: Record<string, T[]> = {};
  for (const item of items) (groups[key(item)] ??= []).push(item);
  return groups;
}
