import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { ApiError } from "./api";
import "./app.css";
import { AdminOnly, NotFound, RouteError } from "./components/Feedback";
import { Shell } from "./components/Shell";
import { FormView } from "./pages/FormView";
import { Home } from "./pages/Home";
import { ListView } from "./pages/ListView";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // Retry network hiccups and server errors, not answers like 404 or 422.
      retry: (failures, error) =>
        failures < 2 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
    },
  },
});

// The builder carries the drag-and-drop and app-map libraries, so it loads only when
// someone opens it: the everyday list and form screens stay small.
const AppMap = lazy(() => import("./pages/AppMap").then((m) => ({ default: m.AppMap })));
const BuilderPage = lazy(() => import("./pages/Builder").then((m) => ({ default: m.BuilderPage })));

const builderRoute = (page: ReactNode) => (
  <AdminOnly>
    <Suspense fallback={<p className="muted">Loading…</p>}>{page}</Suspense>
  </AdminOnly>
);

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Home /> },
      { path: "app/:doctype", element: <ListView /> },
      { path: "app/:doctype/new", element: <FormView /> },
      // The rest of the path is the record's name, which may contain slashes.
      { path: "app/:doctype/*", element: <FormView /> },
      { path: "build", element: builderRoute(<AppMap />) },
      { path: "build/new", element: builderRoute(<BuilderPage />) },
      { path: "build/:doctype", element: builderRoute(<BuilderPage />) },
      { path: "*", element: <NotFound what="This page" /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
