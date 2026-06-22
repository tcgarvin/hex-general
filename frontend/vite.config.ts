import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite SPA config (roadmap Phase 7). React + Tailwind v4 (plugin, no PostCSS),
// `@` aliased to `src`, dev server on 5173 (the origin allow-listed by the API's
// CORS regex and the Google OAuth redirect URIs).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
  },
});
