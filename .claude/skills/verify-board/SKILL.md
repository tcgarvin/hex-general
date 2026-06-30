---
name: verify-board
description: Render the local hex-general board in Chrome to eyeball a frontend/board change — tiles, sprites, fog, overlays. Use after changing board art or rendering code, or when asked to "see it on the board", "does it render", or to screenshot the game. Covers the dev-server check, the dev login (creds location), the board route, and what to capture.
---

# verify-board

The board only renders **after** auth + an active game, so "did my tile/render
change land?" needs a short, fixed browser flow. This skill is that flow — follow
it instead of rediscovering the login wall, the dev password, and the game route
each time. It complements `verify-stack` (that one hits the **live** deployed
stack; this one drives the **local** dev app for a visual check).

## 1. Make sure the dev servers are up

Board is Vite `:5173`, talking to the API `:8080`.

```bash
curl -s -o /dev/null -w "vite:%{http_code} " http://localhost:5173 ; \
curl -s -o /dev/null -w "api:%{http_code}\n"  http://localhost:8080/health
```

- Both `200` → go to step 2.
- Not up → `./scripts/dev.sh up` (detached; don't run the servers in the
  foreground). If it doesn't come healthy, **spawn the `dev-doctor` subagent** to
  diagnose rather than tailing logs here. If the API/DB looks unseeded, the user
  may need `./scripts/dev-up.sh` (Supabase stack + seed). See `docs/LOCAL_DEV.md`.

## 2. Open and sign in (local dev account)

Load the Chrome MCP tools first (one `ToolSearch`, batched — see the
claude-in-chrome instructions), then `tabs_context_mcp`, a new tab, and navigate
to `http://localhost:5173`. You'll land on `/login` ("Local dev sign-in").

- Email is prefilled `dev1@hex.local`. The **password is a documented local test
  fixture**, not a real credential: `DEV_PASSWORD` in `api/app/dev_seed.py`
  (currently `devpass123`; read it rather than hardcoding). `dev2@hex.local` shares
  the same password and is side B.
- Click the password field, type the fixture, click **Sign in as dev**. On success
  the URL becomes `/game/<uuid>` and the board renders. (Empty password →
  "Invalid login credentials".)

If there's no active game for `dev1`, the seed normally creates one; if the app
lands somewhere other than a game, the local stack likely needs
`./scripts/dev-up.sh` to (re)seed.

## 3. Capture the board

- **Batch** your browser calls (`browser_batch`) — clicks/screenshots one at a
  time gets nagged and is slow.
- Full-board `screenshot` for layout/coverage; then `zoom` into a region with a
  mix of the terrains/units you changed to judge crispness at native resolution.
- For a tile/art change, confirm the **art renders** (right tiles, no upscale blur,
  cohesive palette) — that's the real check. A passing `vite build` only proves the
  PNGs are valid and discovered, not that they look right. (At 64px some tiles
  inline as base64 and some emit as `dist/assets/*.png` — a normal mix; don't infer
  anything from grepping the bundle.)
- **Judge at board scale, not just zoomed-in.** The board auto-fits the map, so the
  default view renders hexes small (~50px). A tile that looks great in a montage can
  collapse into muddy "rocky" speckle when shrunk, and a terrain much darker than its
  neighbours reads as a *different material*. Eyeball the full-board view (not only a
  zoom), and sanity-check brightness parity across terrains.

### Tile-change gotchas (learned the hard way)

- **"What terrain is that hex?" → solid-colour probe.** To map which hexes are which
  terrain/variant, temporarily overwrite the variant PNGs with solid colours
  (`PIL Image.new('RGBA',(64,64),(255,0,255,255))` → magenta, etc.), reload, and read
  the board. Instantly disambiguates; restore the real tiles after. (Variants are
  chosen per-hex by a coord hash, so each `{nn}.png` lights up a deterministic subset.)
- **Rule out caching before chasing ghosts.** A full `navigate` reload re-fetches
  tiles; the Vite dev server serves current disk bytes. Confirm with md5:
  `curl -s localhost:5173/src/board/tiles/<terrain>/<nn>.png | md5sum` vs
  `md5sum frontend/src/board/tiles/<terrain>/<nn>.png`. If they match, the browser is
  showing the real file — the look you don't like is the *art*, not a stale cache.
- **Mean luminance is a fast objective check.** A quick PIL opaque-pixel mean of
  `0.299R+0.587G+0.114B` per tile catches "too dark" / "washed out" before you even
  open the browser, and lets you compare terrains numerically.

## Quick reference

| Thing | Value |
|---|---|
| Board URL | `http://localhost:5173` → redirects to `/login`, then `/game/<uuid>` |
| Dev login | `dev1@hex.local` / `DEV_PASSWORD` in `api/app/dev_seed.py` |
| Servers | Vite `:5173`, API `:8080`; managed by `scripts/dev.sh` |
| Tiles | `frontend/src/board/tiles/{terrain}/{nn}.png` (64px, flat-top hex) |
| Montage a tile set | `python3 scripts/tile-montage.py <dir|globs> --out <path>` |
