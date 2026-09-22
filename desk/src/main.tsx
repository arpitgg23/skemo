import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { ApiError } from "./api";
import "./app.css";
import { NotFound, RouteError } from "./components/Feedback";
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
