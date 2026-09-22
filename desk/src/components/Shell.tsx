import { Link, NavLink, Outlet } from "react-router";
import { groupByModule, useDocTypes } from "../meta";

export function Shell() {
  const { data, error } = useDocTypes();
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          Dhancha <span className="muted">desk</span>
        </Link>
        {data && (
          <span className="muted">
            site <code>{data.site}</code>
          </span>
        )}
        <a className="muted" href="/docs" target="_blank" rel="noreferrer">
          API docs
        </a>
      </header>
      <div className="shell-body">
        <nav className="sidebar" aria-label="DocTypes">
          {error && <p className="error-text">Can't load DocTypes: {error.message}</p>}
          {data?.doctypes.length === 0 && <p className="muted small">No DocTypes yet</p>}
          {data &&
            groupByModule(data.doctypes).map(([module, doctypes]) => (
              <div key={module} className="nav-group">
                <p className="nav-module">{module}</p>
                {doctypes.map((dt) => (
                  <NavLink key={dt.name} to={`/app/${dt.name}`} className="nav-link">
                    {dt.label}
                  </NavLink>
                ))}
              </div>
            ))}
        </nav>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
