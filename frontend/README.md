# Hex General — frontend

The browser client: a **Vite + React + TypeScript** single-page app with
client-side routing (React Router), Tailwind v4 styling, Supabase Auth (Google
SSO), and a typed API client generated from the FastAPI OpenAPI schema. See
[`../docs/FRONTEND.md`](../docs/FRONTEND.md) for personas, journeys, and the
page/route structure, and [`AGENTS.md`](./AGENTS.md) for stack conventions.

## Getting started

```bash
npm install
cp .env.example .env.local   # fill in VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY (+ VITE_API_URL)
npm run dev                  # http://localhost:5173
```

By default the app points at the deployed Cloud Run API. Set
`VITE_API_URL=http://localhost:8080` to run against a local API.

## Scripts

- `npm run dev` — Vite dev server (port 5173)
- `npm run build` — type-check + production build to `dist/`
- `npm run lint` — ESLint
- `npm run gen:api` — regenerate the typed API client from the API's OpenAPI
  schema (dumps `../api/openapi.json`, then `src/lib/api/schema.d.ts`). Run after
  changing API request/response models. Requires `uv` (it invokes the API package).

## Auth

Login is Google SSO via Supabase Auth. Real Google OAuth is configured on the
deployed Supabase project; locally a dev can sign in once and use the **dev profile
switcher** (bottom-right, visible to allowlisted devs) to act as any other user for
solo two-sided playtesting. The full local stack (`supabase start`) lands in the
next slice.

## Deploy

Vercel builds the static bundle (`vercel.json` pins the Vite framework, build
command, output dir, and the SPA catch-all rewrite). CI: `.github/workflows/frontend.yml`.
