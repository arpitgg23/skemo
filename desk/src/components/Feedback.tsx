import type { ReactNode } from "react";
import { isRouteErrorResponse, Link, useRouteError } from "react-router";

export function Banner({
  kind = "error",
  children,
}: {
  kind?: "error" | "warning";
  children: ReactNode;
}) {
  return (
    <div className={`banner banner-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
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
