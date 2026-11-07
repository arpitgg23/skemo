import type { ReactNode } from "react";
import { isRouteErrorResponse, Link, useRouteError } from "react-router";
import { useSession } from "../session";

export function Banner({
  kind = "error",
  children,
}: {
  kind?: "error" | "warning" | "success";
  children: ReactNode;
}) {
  return (
    <div className={`banner banner-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

/** Screens only a System Manager should see, such as the builder. The server checks too:
 * this is so nobody is shown a page whose every action would be refused. */
export function AdminOnly({ children }: { children: ReactNode }) {
  const { user } = useSession();
  if (user?.is_admin) return <>{children}</>;
  return (
    <section className="panel">
      <h1>Not allowed</h1>
      <p>Only a System Manager can design DocTypes.</p>
      <Link to="/">Back to the start</Link>
    </section>
  );
}

export function NotFound({ what }: { what: string }) {
  return (
    <section className="panel">
      <h1>Not found</h1>
      <p>{what} doesn't exist.</p>
      <Link to="/">Back to the start</Link>
    </section>
  );
}

/** Shown instead of a blank page when rendering throws. */
export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error);
  return (
    <section className="panel" style={{ margin: 24 }}>
      <h1>Something broke</h1>
      <pre>{message}</pre>
      <Link to="/">Back to the start</Link>
    </section>
  );
}
