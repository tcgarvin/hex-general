# UI style — "Field HQ" (ratified direction)

The visual identity for the game's UI chrome — panels, buttons, type, icons,
decoration — for both the Pixi game-screen chrome (UI-SPEC §2) and the React
DOM surfaces (header, auth/dispatcher). Ratified 2026-07-07 from a three-way
concept review; the ratified concept is
[`concept-art/ui-concept-a.png`](./concept-art/ui-concept-a.png) (prompt
provenance in `art/foundry/ui/`).

Where this document and [`UI-SPEC.md`](./UI-SPEC.md) disagree on *layout or
behavior*, UI-SPEC wins; this document owns *look and material*. It refines
UI-SPEC §14 (which sketched a generic "dark steel" placeholder before the
direction was chosen).

## 1. The register

A mid-20th-century **field headquarters map table**: dark-stained oak and
walnut framing, polished brass fittings with slotted screws, aged parchment
faces, worn olive canvas and saddle leather, engraved nameplates, chunky
brass-and-bakelite pushbuttons that visibly depress. Warm lamplight, not
cockpit glow.

Hard rules:

- **Chrome only.** The map keeps its ratified naturalistic tileset look. The
  concept image shows a warm/sepia cast over the terrain — that part is *not*
  ratified. No warm color grading, vignettes, or parchment filters over the
  board.
- **The map stays dominant** (UI-SPEC §1). Material richness lives in the
  frames and controls; panel *faces* stay quiet so content reads first.
- Decoration is **structural**: brackets at corners, screws on rails, plates
  behind titles. Never scatter rivets/straps as free-floating ornament.

## 2. Palette tokens

Sampled from the ratified concept (chrome regions only). These are the seed
values for a shared theme module (`frontend/src/theme.ts`, replacing the
per-panel constants in `board/panels/*`); expect small adjustments as real
components land.

| Token | Hex | Role |
| --- | --- | --- |
| `wood-900` | `#321f14` | darkest wood (header bar, panel outer rails) |
| `wood-700` | `#3d2d1b` | primary wood frame tone |
| `wood-500` | `#665036` | wood highlight grain |
| `brass-600` | `#a1793e` | brass base |
| `brass-400` | `#d9b060` | brass highlight / lit edges |
| `parchment-200` | `#e4cda2` | parchment face (light panel fill) |
| `parchment-400` | `#aa9675` | parchment shadow / aged edge |
| `canvas-800` | `#4c2e21` | leather/canvas dark panel fill |
| `canvas-600` | `#7f5c37` | leather mid tone |
| `ink-900` | `#2b2118` | text on parchment (warm near-black ink) |
| `ivory-100` | `#f0e6d2` | text on dark wood/canvas |
| `ivory-400` | `#a89880` | secondary text on dark |
| Status | — | amber warning `#f5b342` · invalid red `#c03b2e` · valid `#7fa65a` |
| Factions | — | keep engine blue `#4f8cff` / red `#ff5d5d` (board consistency) |

Two panel-face modes, both in the concept: **parchment faces** (roster list,
inspector — dark ink text) and **dark canvas/leather faces** (dock strip,
minimap surround — ivory text). Use parchment where the player reads dense
data; canvas/leather where controls sit. The two combine on the dock: the
selected unit's identity block is a wood-framed **parchment card inset into
the canvas strip** (ratified 2026-07-07), with the order palette and advisory
text on the canvas beside it.

## 3. Typography (three roles)

All OFL, self-hosted woff2 under `frontend/src/theme/fonts/`; loaded via
`@font-face` + `document.fonts.load()` **before** the Pixi app draws text
(Pixi rasterizes at draw time — an unloaded font silently falls back). The
React header and auth screens use the same families.

| Role | Use | Ratified pick |
| --- | --- | --- |
| Display | panel titles, LOCK ORDERS, banners, brand | **Allerta Stencil** (ratified 2026-07-07) |
| Label (condensed sans) | buttons, tabs, header status chips | **Archivo Narrow** (ratified 2026-07-07) |
| Data (typewriter) | roster rows, coords, HP, stats, event log | **Special Elite** (ratified 2026-07-07). **Not fixed-advance** — accepted deliberately. Consequence: data columns must be aligned by *layout* (each column its own Text at a fixed x), never by space-padding one string. |

Conventions regardless of pick: labels are UPPERCASE with slight
letter-spacing; data columns use tabular figures; no font renders below ~11px
in the Pixi panels.

## 4. Component treatments

- **Panels** — 9-slice wood frame with brass **L-shaped corner brackets**
  (round-1 candidate 1 construction, ratified 2026-07-07); parchment or canvas
  face fill (tileable texture, low contrast). Panel titles sit on an engraved
  brass nameplate strip.
- **Tabs** — folder tabs; active = parchment face + ink text, inactive = dark
  canvas + ivory text (concept's UNITS/CONTROL treatment).
- **Buttons** — **squared, clunky, machine-press**: flat brushed-brass faces
  with crisp machined chamfers on screwed dark-wood backplates — explicitly
  *not* rounded ergonomic keycaps (user direction, 2026-07-07). Icon engraved
  on the brass face; stencil label engraved on/next to the backplate. Five
  states: up · hover (warm rim glow) · pressed (face sinks ~2px, chamfer
  inverts, shadow collapses) · disabled (desaturated) · active-lit (pressed +
  amber lamp, marks the current order mode). Three sizes: order button, square
  icon button, and the wide **LOCK ORDERS** commit button (engraved stencil
  lettering, brass safety-guard flavor) with LOCKED / FIX ORDERS states.
  Ratified construction (2026-07-07, round-2 candidate 2): **octagonal
  machined chamfer on the brass face, label engraved into the wood backplate
  directly beneath the face** — icon + label are one physical unit.
- **Icons** — single-color tintable glyph sheet (orders, NATO-flavored unit
  types, status, replay transport, economy) — dark ink on parchment, ivory on
  dark faces, amber/red for warnings.
- **Wells** — portrait, minimap, and event log sit in recessed brass-lipped
  inset frames.
- **DOM surfaces** — header/auth reuse the same 9-slice PNGs via CSS
  `border-image`, same tokens via Tailwind theme values. No parallel design
  system.

## 5. Asset production path

Same discipline as the tile foundry (`docs/ART_PIPELINE.md`): NBP
(`scripts/tile-foundry/gen.py`, ref = the ratified concept) generates **UI-kit
sheets** on neutral gray → deterministic cut/9-slice/atlas tooling in
`scripts/` → montage → **Artifact review; the user ratifies each component
family before it's applied** (panels, buttons, icons, fonts are dialed in
individually). Provenance (prompts + picked sheets) lands in
`art/foundry/ui/`.
