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

Routes: `/login` (Google SSO; against a local stack, the dev email/password form with its "Sign in as dev" button), `/stats` (public), `/` (dispatcher: redirects into the caller's active world, else lists worlds to join), `/worlds` (the same world list without the redirect; a seated player's own world is marked "You are here"; the world header's wordmark links here), `/account` (email, your world, sign out), `/admin` (`src/admin/`, admins only per `GET /me`'s `is_admin`, linked from the account menu and the worlds page: every world with its seats and clock, create, resolve a turn, delete, run the clock sweep, all confirmed in the page; anyone else sees a "not for this account" notice and the API answers 403; admins also get "Resolve turn now" in the world screen's menu, desktop and phone, via `admin/ResolveTurnItem.tsx`), `/world/:id` (`src/world/`, the game; a 403/404 there shows a "closed to you" notice instead of the board). The page look follows `docs/UI-STYLE.md` (wood header, parchment cards, brass buttons from `components/ui.tsx`); the login and start screens sit on `.fhq-backdrop` in `index.css`, which draws the ratified sign-in key art (`src/ui-art/keyart/`, set as `--fhq-key-art`). UI glyphs go through `components/Glyph.tsx` and the emblem through `components/Brand.tsx`; their files in `src/ui-art/` and `public/` (favicons) are built by `art/codex/ui/apply_ui_art.py`, so change them there (docs/UI-STYLE.md §4). Board helpers the world view reuses live in `src/board/`, `src/planning/` and `src/replay/`.

### World screen on a phone

`WorldScreen` (`src/world/WorldShell.tsx`) composes all shared state once (planning,
report, playback, designations, forecast) and picks the layout at render with
`usePhoneLayout` (`src/world/mobile/`): below 768 px wide, or a coarse pointer at most
500 px tall (a phone sideways), it renders `mobile/PhoneWorld.tsx`; otherwise the desktop
JSX, unchanged. The phone layout is a top bar (turn, countdown, stocks and army as glyph +
number, a menu), the map full screen (no minimap or zoom keys; `WorldRenderer.setChrome`
hides the minimap and sets the camera insets), a bottom bar (Army, Report, Base, the
Submit plate) whose keys open bottom sheets, the unit dock when one of your units is
selected, and the compact `PlaybackBar` while a turn plays. An order is two steps: select
the unit, press the dock's Move key (or Barrage for guns, or a stop from More), then tap.
`useWorldPlan(..., auto = true)` adds `onHexTap` and `arm`; the rule is the pure
`world/autoOrder.ts`. Unarmed, a tap never orders: it selects your unit there or inspects
the hex (the move reach is lit only while Move is armed). With Move armed it orders as if
none of your units stood on the hex: move within reach (an attack onto a hostile, which
engages by default; guns can't attack), move toward a far hex from inside the contact zone, else travel with an
automatic stop short of contact; an order disarms, a notice keeps Move armed, and the
unit's own hex disarms. There is no long-press (Shift+click on desktop orders onto your
own unit's hex). Your own units never make a hex unreachable (`planMoveReach` in `world/moveRules.ts`); a Move
they will block is staged and warned about (`planConflicts`), and two units ordered into
each other's hex swap. The dock's text is `mobile/readout.ts`. Shared
words and small parts live in `world/labels.ts`, `world/parts.tsx` and `world/useSwaps.ts`.
Line icons with no glyph yet (Move, Hold, Rest, Barrage, Clear, Report, More, menu, close,
chevrons) are placeholders in `mobile/icons.tsx`. Check it at phone width with the dev
harness `/phone-frame.html?path=/world/<id>` (docs/FRONTEND.md).

The desktop layout works the same way: a selected unit opens in a neutral "select" mode,
so a plain click on empty ground deselects the unit and selects the hex, and a click only
orders after Travel, Move or Barrage is chosen (panel chip or T / M / B). The pick is one
shot: an order goes back to select, a notice keeps it (`world/clickOrder.ts`).

Attacks (WORLD_SPEC §4.8): a Move whose path ends on a visible hostile hex is staged as an
Engage (`moveOrder` in `world/plan.ts`); the desktop unit panel and the phone dock show an
Assault / Engage toggle (`AttackToggle` in `world/parts.tsx`, `setAttack` on the planning
hook). Any other Move sends assault, the engine default. The plan draws an engagement's line
stopping beside the target with a dashed strike (`drawEngageStrike` in `WorldRenderer.ts`).

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
