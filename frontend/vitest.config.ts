import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

// Standalone Vitest config. The replay beat-engine tests are pure logic (no DOM,
// no React/Tailwind), so this intentionally omits the Vite plugins from
// `vite.config.ts` — it only needs the `@` → `src` alias to resolve imports.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
