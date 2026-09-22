import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API runs separately (`dhancha serve`); the desk reaches it through this proxy.
const api = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": api, "/docs": api, "/openapi.json": api },
  },
});
