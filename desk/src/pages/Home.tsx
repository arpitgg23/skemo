import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, type DocType, type LogEntry } from "../api";
import { Banner } from "../components/Feedback";
import { usePageTitle } from "../hooks";
import { groupByModule, useDocTypes } from "../meta";

export function Home() {
  usePageTitle("Home");
  const doctypes = useDocTypes();
  const log = useQuery({ queryKey: ["schema-log"], queryFn: () => api.schemaLog(15) });
  if (doctypes.error) return <Banner>{doctypes.error.message}</Banner>;
  if (!doctypes.data) return <p className="muted">Loading…</p>;
  return (
    <>
      <AppStructure doctypes={doctypes.data.doctypes} />
      {log.data && <SchemaLog entries={log.data.entries} />}
    </>
  );
}

function AppStructure({ doctypes }: { doctypes: DocType[] }) {
  if (doctypes.length === 0) {
    return (
      <section className="panel">
        <h1>No DocTypes yet</h1>
        <p>Create some from JSON, then come back to this tab:</p>
        <pre>skemo save-doctype examples/crm</pre>
      </section>
    );
  }
  return (
    <section>
      <h1>App structure</h1>
      <p className="muted">
        Every form below is a DocType. The right-hand column is the column that really exists in
        Postgres. Open one to add records.
      </p>
      <div className="modules">
        {groupByModule(doctypes).map(([module, items]) => (
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
        <Link to={`/app/${doctype.name}`}>
          <strong>{doctype.label}</strong>
        </Link>
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
