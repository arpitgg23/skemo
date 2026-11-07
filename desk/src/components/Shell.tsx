import { Link, NavLink, Outlet } from "react-router";
import { groupByModule, useDocTypes } from "../meta";
import { Login } from "../pages/Login";
import { useLogout, useSession } from "../session";
import { Banner } from "./Feedback";

export function Shell() {
  const session = useSession();
  if (session.isLoading) return <p className="muted loading">Loading…</p>;
  if (session.error) {
    return (
      <div className="login">
        <Banner>{session.error.message}</Banner>
      </div>
    );
  }
  // Nobody signed in: the whole desk is the sign-in page, and the URL is kept, so
  // signing in lands you back where you were.
  if (!session.user) return <Login />;
  return <Desk />;
}

function Desk() {
  const { user, site } = useSession();
  const logout = useLogout();
  const { data, error } = useDocTypes();
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">
          Dhancha <span className="muted">desk</span>
        </Link>
        {site && (
          <span className="muted site-name">
            site <code>{site}</code>
          </span>
        )}
        {user?.is_admin && (
          <NavLink to="/build" className="top-link">
            Builder
          </NavLink>
        )}
        <a className="muted" href="/docs" target="_blank" rel="noreferrer">
          API docs
        </a>
        <span className="muted" title={user?.roles.join(", ") || "no roles"}>
          {user?.label}
        </span>
        <button type="button" onClick={() => logout.mutate()} disabled={logout.isPending}>
          Sign out
        </button>
      </header>
      <div className="shell-body">
        <nav className="sidebar" aria-label="DocTypes">
          {error && <p className="error-text">Can't load DocTypes: {error.message}</p>}
          {data?.doctypes.length === 0 && (
            <p className="muted small">
              {user?.is_admin ? "No DocTypes yet" : "Nothing shared with you yet"}
            </p>
          )}
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
