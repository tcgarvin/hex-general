# Frontend — Vite + React SPA

A client-side-routed single-page app (roadmap Phase 7). **Not Next.js** — the
create-next-app scaffold was replaced; there is no server, no app router, no
`next/*` imports.

## Stack

- **Vite** (build/dev) · **React 19** · **TypeScript** · **React Router v8**
  (`react-router`, declarative `<BrowserRouter>`/`<Routes>`) · **Tailwind v4**
  (via `@tailwindcss/vite`, no PostCSS config).
- **Supabase Auth** holds the session (`@/lib/supabase`); the app sends the JWT to
  the API. **openapi-fetch** is the typed API client (`@/lib/api/client`).

## Env vars

Browser config is `VITE_`-prefixed and read via `import.meta.env`
(`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`). Vite inlines an
unset `VITE_` var as `""`, so default with `||`, never `??`.

## Typed API client — regenerate after API schema changes

`src/lib/api/schema.d.ts` is generated from the API's OpenAPI schema (the single
source of truth for `GameView`/`Order`/…). After changing API request/response
models, regenerate:

```bash
npm run gen:api   # dumps ../api/openapi.json, then regenerates schema.d.ts
```

Do not hand-edit `schema.d.ts`. Import model types from `@/lib/api/types`.

## Commands

`npm run dev` (port 5173) · `npm run build` (`tsc -b && vite build`) · `npm run lint` ·
`npm run test` (Vitest, pure-logic unit tests under `src/**/*.test.ts`).
