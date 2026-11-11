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

## Screens

Routes: `/login` (Google SSO; against a local stack, the dev email/password form with its "Sign in as dev" button), `/stats` (public), `/` (dispatcher: redirects into the caller's active world, else lists worlds to join), `/worlds` (the same world list without the redirect; a seated player's own world is marked "You are here"; the world header's wordmark links here), `/account` (email, your world, sign out), `/world/:id` (`src/world/`, the game; a 403/404 there shows a "closed to you" notice instead of the board). The page look follows `docs/UI-STYLE.md` (wood header, parchment cards, brass buttons from `components/ui.tsx`); the login and start screens sit on `.fhq-backdrop` in `index.css`, which draws the ratified sign-in key art (`src/ui-art/keyart/`, set as `--fhq-key-art`). UI glyphs go through `components/Glyph.tsx` and the emblem through `components/Brand.tsx`; their files in `src/ui-art/` and `public/` (favicons) are built by `art/codex/ui/apply_ui_art.py`, so change them there (docs/UI-STYLE.md §4). Board helpers the world view reuses live in `src/board/`, `src/planning/` and `src/replay/`.

## Env vars

Browser config is `VITE_`-prefixed and read via `import.meta.env`
(`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_URL`). Vite inlines an
unset `VITE_` var as `""`, so default with `||`, never `??`.

## Typed API client — regenerate after API schema changes

`src/lib/api/schema.d.ts` is generated from the API's OpenAPI schema (the single
source of truth for `WorldView`/`Order`/…). After changing API request/response
models, regenerate:

```bash
npm run gen:api   # dumps ../api/openapi.json, then regenerates schema.d.ts
```

Do not hand-edit `schema.d.ts`. Import model types from `@/lib/api/types`.

## Commands

`npm run dev` (port 5173) · `npm run build` (`tsc -b && vite build`) · `npm run lint` ·
`npm run test` (Vitest, pure-logic unit tests under `src/**/*.test.ts`).
