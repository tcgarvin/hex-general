import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite SPA config (roadmap Phase 7). React + Tailwind v4 (plugin, no PostCSS),
// `@` aliased to `src`. Dev server defaults to 5173 (the origin allow-listed by
// the API's CORS regex and the Google OAuth redirect URIs) but reads
// VITE_DEV_PORT so `scripts/dev.sh` can run per-worktree instances on offset
// ports (5173 + 100×instance). The API's CORS allows any http://localhost:<port>.
// Vitest config lives separately in `vitest.config.ts` (the unit tests are pure
// logic and don't need the React/Tailwind plugins).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: Number(process.env.VITE_DEV_PORT) || 5173,
  },
});
