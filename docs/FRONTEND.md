# Frontend Spec — hex-general v0.1

Personas, journeys, and page structure for the browser client. Implements the UX
requirements in [`SPEC.md`](./SPEC.md) §16 across roadmap Phases 7–10
([`ROADMAP.md`](./ROADMAP.md)). Read this before designing screens; it sets *who*
the frontend is for and *what loop* it serves.

## The one fact that shapes everything: this is async

Play is **async two-browser**, not real-time. A player's relationship with the app
is a **series of visits**, not one sitting. After turn 1 the dominant mode is
"come back later, see what happened, plan the next turn." Every design choice
favors that loop over live interaction.

## Audience

**You + recruited friends** — a playtest tool, not a public product. There is no
matchmaking, no public lobby, no tutorial. Invitees get a link from someone who
can explain the game. The polish budget goes to **replay clarity** and
**observability**, not onboarding.

## Personas

| # | Persona | Who | Cares about |
|---|---------|-----|-------------|
| **P1** | **The Host** | Starts the match (you, or a recruited friend) | Trivial create, a link that works, confidence the other side gets in |
| **P2** | **The Invitee** | Got a link, may not know the game | Zero-friction join, enough to take a competent first turn (SPEC §18) |
| **P3** | **The Returning Async Player** | P1/P2 on a later visit — the **dominant** mode after turn 1 | "What changed while I was gone, and what's my move," crisp *your-move* vs *waiting-on-them* |
| **P4** | **The Developer/Playtester** | You, builder hat | Driving **both** sides, raw event log, determinism, running the loop on a laptop |

P1/P2 differ only at the entry point — both become P3 immediately. P4 gets no
dedicated pages but shapes the **dev profile switcher** and local-dev story.

## Major journeys

- **J1 Start a match** (P1): log in → create → land in game → copy shareable invite
  link → wait for opponent. *(Phase 7)*
- **J2 Join a match** (P2): open invite link → log in → claim the open slot → land
  in the game. *(Phase 7)*
- **J3 The turn loop** — the heartbeat, repeats every turn for everyone:
  arrive → **orient** (a turn resolved since last visit? watch the replay) → read
  the fog-filtered board → **plan** (paths, barrage/rest/hold, recruit) → review +
  **lock** → **wait** (opponent still planning) → resolution → back to orient.
  *(scaffolded P7; board P8; plan P9; replay P10)*
- **J4 Resume** (P3): reopen the app → already logged in → dispatched straight to
  your active game. *(Phase 7)*
- **J5 Endgame** (all): victory / defeat / draw, final board + final replay.
  *(P10/11)*
- **J6 Developer observe** (P4): flip the dev profile switcher to drive both sides,
  inspect raw events, reset/seed scenarios on the local stack. *(cross-cutting)*

## Identity & accounts

Lightweight SSO accounts via **Supabase Auth (Google)** — chosen over the roadmap's
original localStorage-opaque-token scheme because the audience is friends and
accounts give a clean "log in, land back in your game" loop.

- **Frontend** holds the Supabase session (publishable/anon key + project URL). The
  Supabase JS client handles the OAuth redirect and token refresh.
- **API** validates the Supabase **JWT** on each request (`Authorization: Bearer …`)
  to identify the user. This is boundary validation per the CLAUDE.md standard —
  reject with a typed error if the token is missing/invalid/expired.
- `players` gains a **`user_id`** (the Supabase user). The Phase 5 opaque
  `X-Player-Token` becomes an internal slot reference or retires.
- **One active game per user.** A user maps to at most one un-finished `players`
  row, which makes login a *dispatcher* (below). Starting/joining a new game
  requires no active game.
- **Invite link** carries the game id + an unguessable invite token (game ids are
  guessable; the open slot must not be claimable by randos). It does **not** carry a
  player secret — identity comes from SSO.

## Page / route structure

One-game-per-user keeps the route table tiny. The **game shell** is the
load-bearing component: the polling hook + game-context provider live there, and
every later phase mounts inside it.

| Route | Purpose | Journey | Phase |
|-------|---------|---------|-------|
| `/login` | Google SSO gate | P1/P2 entry | 7 |
| `/` | **Dispatcher** — active game? → `/game/{id}` : → start screen (Create / paste-join) | J1, J4 | 7 |
| `/join/{id}#invite=…` | Log in → claim open slot → redirect to game | J2 | 7 |
| `/game/{id}` | **Game shell**: persistent context. P7 = status dashboard (turn #, both slots, your slot, your-move vs waiting banner, share link while a slot is open). Board (P8) + planning (P9) mount here. | J3 | 7→9 |
| `/game/{id}/replay/{turn}` | Step-through replay with §16.3 combat explanations | J3 orient, J5 | 10 |
| corner overlay | Dev profile switcher (shown to dev-role users) | J6 | 7 |

The "lobby" collapses to **login → dispatcher → create-or-join → game shell**.

## Live updates: polling, not Realtime

Per roadmap decision 8, the client learns turn progress by **polling**
`GET /games/{id}` on an interval (no Supabase Realtime). When `turn`/`status`/
`locked[slot]` advances, fetch the replay. One data path (the REST API).

## Local dev playtesting

A dev must be able to play a full match on a laptop without touching the deployed
stack or configuring Google OAuth.

- **Local stack:** `supabase start` runs Postgres + Auth + Studio in Docker from
  `supabase/config.toml`; migrations in `supabase/migrations/` apply locally. It
  prints a local API URL, anon key, and service-role key.
- **Wiring:** the local API (`uv run uvicorn …`) points `SUPABASE_URL` /
  `SUPABASE_SERVICE_ROLE_KEY` at the local stack; the Vite dev server points
  `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` at the local stack and
  `VITE_API_URL` at `http://localhost:8080`. *(Today `frontend/.env.local` points
  at the remote project — the re-scaffold redirects local dev at the local stack so
  playtesting never dirties shared/deployed state.)*
- **Auth locally:** real Google OAuth (redirect URIs, client secret) is configured
  **only on deployed envs**. Local playtesting signs a seeded dev user into the
  **local** Supabase Auth (no OAuth round-trip), then the **dev profile switcher**
  impersonates the other seeded identities. The switcher is **not** local-only — it
  works on the deployed stack too, gated by the dev role (below).
- **Seed:** a seed script creates the dev users (+ optionally a starter game) so a
  fresh `supabase start` is immediately playable.

## Deviations from the original roadmap

- **Identity:** SSO accounts (Supabase Auth/Google) replace localStorage opaque
  token + secret-in-link. Adds backend work (JWT validation, `players.user_id`) the
  v0.1 framing had deferred — the part of Phase 7 most likely to balloon. **Shipped**
  (backend slice): JWT verified via the project JWKS (ES256), `players.user_id`,
  invite-gated join, `GET /games/active` dispatcher.
- **Dev switcher on deployed, gated by a role:** revises the original "deployed API
  rejects the dev path." A *very lightweight* role system instead — a dev is a user
  whose id is in the API's `DEV_USER_IDS` allowlist (no DB role table); a dev (only a
  dev) may impersonate another user via the `X-Dev-Profile` header (403 otherwise).
  The path exists everywhere; non-devs are rejected. **Shipped** (backend slice).
- **Env prefix:** `NEXT_PUBLIC_` → `VITE_` (Vite inlines `VITE_`-prefixed vars).
  The `||`-not-`??` default rule (CLAUDE.md) still applies — unset vars inline as
  `""`. **Shipped** (Slice 2).
- **Vite SPA re-scaffold:** the create-next-app boilerplate is replaced by a Vite +
  React + TS + Tailwind v4 SPA (React Router); login/dispatcher/join/game-shell +
  `/stats`, a typed API client (`openapi-fetch` over generated types), polling game
  view, and the dev profile switcher (gated by `GET /me`'s `is_dev`). **Shipped**
  (Slice 2).
- **Local dev:** Phase 7 now explicitly owns the `supabase start` laptop loop, not
  just the deployed path. **Shipped** (Slice 3): local ES256 signing keys keep the
  API's JWKS auth boundary unchanged, `app.dev_seed` seeds the `@hex.local`
  accounts, and `scripts/dev.sh up` wires it together (per-worktree isolated). See
  [`LOCAL_DEV.md`](./LOCAL_DEV.md).
