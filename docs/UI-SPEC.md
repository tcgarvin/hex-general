# Hex Wargame UI Spec (game screen)

The design for the **game screen** — the full-page board where a player plans a
turn, locks it, and reviews the replay. This is the UI layer over the engine and
API described in [`SPEC.md`](./SPEC.md) (rules), [`ROADMAP.md`](./ROADMAP.md)
(phases), and [`FRONTEND.md`](./FRONTEND.md) (app shell). Where this document and
`SPEC.md` disagree, `SPEC.md` wins — flag the drift and fix it here.

> This spec was distilled from an external UI draft and reconciled against the
> v0.1 rules. The draft was good on layout and information design but invented
> mechanics that do not exist in this game (notably **Command Points**) and used
> placeholder flavor (named units like "M4 Sherman", `(x,y)` coords). The
> corrections are tracked in [§16](#16-corrections-from-the-source-draft).

---

## 1. Goals

Make the board dominant and the UI quiet. The chrome exists to answer four
questions, in priority order:

1. **Which of my units still need orders?** — the core organizing principle. In
   this game, losing track of one idle artillery unit is more costly (and more
   personally annoying) than losing a town. The whole planning UI is built around
   making "who is unordered?" impossible to miss.
2. **What do I control, and what am I selecting?**
3. **What will my planned orders do** (paths, contact, traffic, ZOC)?
4. **Am I ready to lock?**

Aesthetic target: a classic PC-wargame command desk wrapped around a readable,
zoomed-out map. Old wargame UI — good. Cockpit-simulator UI — bad. The map should
never feel squeezed by chrome.

---

## 2. Layout: thin React header over one full PixiJS surface

**Decision:** The game screen is a thin **React** header at the very top, and
everything below it is a **single PixiJS surface** that fills the rest of the
viewport. The map *and all the chrome* — unit roster, selected-unit detail,
terrain inspector, order palette, minimap, lock control — are drawn inside that
one Pixi application. React's job on this screen is the header (site nav +
turn/phase/resource status) and nothing else; the panels are **Pixi-rendered**,
not React DOM.

```text
┌──────────────────────────────────────────────────────────────┐
│ THIN HEADER (React)  nav · turn/phase · muster · orders · warn │  ~48px, opaque
╞══════════════════════════════════════════════════════════════╡
║ ┌─Roster────┐                                  ┌─Inspector──┐ ║
║ │ Units /   │                                  │ hovered    │ ║   <- everything
║ │ Control   │              MAP                 │ hex / unit │ ║      below the
║ │           │           (hex tiles)            └────────────┘ ║      header is
║ └───────────┘                                                 ║      ONE PixiJS
║ ┌─Selected unit + order palette────────┐  ┌─Minimap─┐ ┌Lock─┐ ║      application
║ └──────────────────────────────────────┘  └─────────┘ └─────┘ ║
╚══════════════════════════════════════════════════════════════╝
   side + bottom panels are Pixi chrome that reserve space (the map shrinks)
```

Panels are **docked** (they reserve space and shrink the map's draw area), not
floating overlays — having the map a little smaller is acceptable for now. They
are part of the Pixi scene graph (`Container` / `Graphics` / `Text`, or a Pixi-UI
layer), laid out relative to the canvas size. The map renders into the remaining
rectangle; pan/zoom and hit-testing operate within that rectangle.

**Later (not v0.1-blocking):** a "fullscreen map" toggle that collapses/hides the
docked panels so the map expands to the full Pixi surface. Design panel layout so
this is a clean addition (panels keyed off a visibility flag), but don't build the
toggle yet.

Why all-Pixi below the header: it keeps one render/input/coordinate system for the
whole board surface (no React-DOM-over-canvas seam for hit-testing, theming, or
world-space tracking), and the panels share the board's pixel-art look. The
trade-off is that text, focus, and accessibility are hand-rolled in Pixi rather
than free from the DOM — accept that for the command-desk aesthetic and single
input surface.

> **Migration note:** the current screen does the opposite — `GameShell.tsx`
> renders React panels around a boxed `<Board>` (`h-[60vh]`). This spec replaces
> that: the React chrome collapses to the thin header, and the panels move into
> Pixi. The planning logic in `usePlanning.ts` / `reach.ts` is UI-agnostic and
> carries over; only its *rendering* moves from React DOM into the Pixi scene.

Coordinates are axial **`(q, r)`** everywhere (the engine's coordinate system;
`unit.coord.q` / `.r`). Never `(x, y)`.

---

## 3. Game-screen states the UI must handle

The board screen is not always "Planning." The header and panels adapt to the
lifecycle (`GameView.status`, `locked[slot]`, `victory.reason`):

| State              | Condition                                              | UI emphasis                                              |
| ------------------ | ------------------------------------------------------ | ------------------------------------------------------- |
| **Lobby**          | `status == "lobby"`                                    | "Waiting for opponent" + invite link; map may be hidden |
| **Your move**      | active, ongoing, `locked[you] != true`                 | Full planning panels; roster, palette, lock control live |
| **Waiting**        | active, ongoing, `locked[you] == true`                 | Read-only board; "Locked in — waiting on opponent…"     |
| **Replay**         | a new resolved turn is available to step through       | Replay panel (Phase 10): scrubber + event log over board |
| **Game over**      | `victory.reason != "ongoing"`                          | Outcome banner; board read-only                          |

This is async two-player: there is no single-player "End Turn → resolve now."
Locking submits your orders; the server resolves only once **both** players lock.
See [§11](#11-lock-control-not-end-turn).

---

## 4. Thin header (top status bar)

Compact, status-heavy, opaque, ~48px. Left-to-right:

```text
[hex-general]   TURN 7 / 24   PLANNING   MUSTER 12/20 (+5)   ORDERS 14/18   ⚠ 3 IDLE      [Account ▾]
```

| Element     | Source / meaning                                                        |
| ----------- | ----------------------------------------------------------------------- |
| Brand / nav | Link home (dispatcher) + account menu (the only required site nav here) |
| Turn        | `view.turn` / scenario turn limit (omit "/ limit" if none)              |
| Phase       | One of: Planning · Locked · Replay · Game over (from §3)                |
| Muster      | Player bank `N / 20` (`Player.bank`, cap 20) and **net income** `(+X)`  |
| Orders      | `units with a staged order / total of your units` — the headline count  |
| Warnings    | Aggregate issue chips; clicking one cycles the affected units/hexes      |

There is **no Command Points field** — that resource does not exist in v0.1 (see
[§16](#16-corrections-from-the-source-draft)).

Warning chips (amber) are clickable and cycle focus through the offenders:

```text
⚠ 3 IDLE      ⚠ 2 TRAFFIC RISK      ⚠ 1 ZOC-LOCKED PATH      ⚠ 1 INVALID
```

Warning kinds, all advisory (planning predictions can be wrong — hidden enemies
and simultaneous enemy moves change outcomes; SPEC §16.1):

- **Idle** — your unit has no staged order.
- **Traffic risk** — two of *your own* staged paths target the same hex on the
  same pulse (friendly traffic jam, SPEC §8.2).
- **ZOC-locked** — a staged path attempts an illegal ZOC→ZOC step (SPEC §8.4).
- **Invalid** — path off-map / through impassable terrain / barrage out of range,
  etc. (mirror server boundary validation, `api/app/games/validation.py`).

---

## 5. Roster panel — the "needs orders" checklist

Docked Pixi panel (left edge). Two tabs; resist adding more early ("tiny Excel
wearing a helmet").

```text
[ Units ]  [ Control ]
```

### 5.1 Units tab

Answers: what do I have, where, what did I order it to do, **which still need
orders**, which are damaged, which are threatened.

Dense, monospaced rows (monospace keeps coords / HP / order labels aligned):

```text
! [INF] (08,31)  —        10/10
  [ARM] (10,29)  MOVE     10/10
  [ART] (12,28)  BARRAGE   8/10
! [INF] (17,26)  HOLD      4/10
  [ARM] (22,22)  ATTACK    9/10
```

Columns: type icon · `(q,r)` · order label · HP. A leading marker column carries
warning glyphs.

**Default sort** (so most players never touch sorting):

1. Unordered units (top, always)
2. Units with warnings (invalid / ZOC-locked / traffic / threatened)
3. Selected unit
4. Damaged units
5. Remaining, grouped by type

Optional controls (secondary): `Sort: Needs orders · Type · HP · Coord` and
`Filter: All · Idle · Damaged · Warnings`.

Row states: selected = bright outline; no order = dim amber marker; valid order =
small check; invalid = red; traffic = amber triangle; in enemy ZOC = red edge;
damaged = obvious missing HP. **Click a row** → select the unit and, if it isn't
already comfortably in frame, smoothly glide the camera until it's centred (a unit
already well inside the view is left where it is — no jarring recenter). See §9 for
the comfort rule. **Double-click** → jump to it without changing the current order mode.

### 5.2 Order labels

Display labels map from engine order kinds (the engine has **no Attack order** —
infantry/armor attack as a consequence of a Move into an enemy-occupied hex;
artillery uses Barrage and may never attack-by-move, SPEC §5.4, §7):

| Engine state                                 | UI label    |
| -------------------------------------------- | ----------- |
| No order                                     | `—`         |
| `Move`, path ends on an empty hex            | `MOVE`      |
| `Move`, path enters/ends in enemy contact    | `ATTACK`    |
| `Barrage` (artillery)                        | `BARRAGE`   |
| `Rest`                                        | `REST`      |
| `Hold`                                        | `HOLD`      |
| Staged order fails client validation         | `INVALID`   |

`ATTACK` is a derived display affordance only — it is still a `Move` order on the
wire. Artillery never shows `ATTACK`.

### 5.3 Control tab

Answers: what settlements I control, my economy, where I can recruit, what's
threatened.

```text
CONTROL
Cities: 3        Towns: 5
Gross +19   Upkeep -11   Net +8
Bank: 12 / 20

[City] (09,31)  +3   Safe      ⊕ recruit
[Town] (17,24)  +2   Threatened
[City] (28,36)  +3   Enemy ZOC
```

Economy values come straight from SPEC §6: town `+2` / city `+3` muster; upkeep
inf `1` / armor `2` / arty `2`; net = income − upkeep added to a bank capped at
20 (overflow lost). The recruit affordance shows **only on friendly cities** —
v0.1 deploys at empty friendly *cities* only, never towns (SPEC §6.5). Status:
Safe · Threatened (adjacent enemy / in enemy ZOC) · Contested. Click a settlement
→ center the map on it.

---

## 6. Selected-unit detail panel

The most old-school panel: portrait/icon left, stats right. Identify units by
**type** (+ a short id when disambiguation helps) — there are no named units in
v0.1. Use 5 HP pips (each pip = 2 HP) plus the exact value.

```text
ARMOR                         INFANTRY                      ARTILLERY
(10,29)                       (12,08)                       (20,18)
HP ■■■■■ 10/10                HP ■■■■■ 10/10                HP ■■■□□ 6/10
Move 4   Atk 7   Def 4        Move 2   Atk 4   Def 5        Move 1   Atk 5   Def 2
Range 1  Vision 2  Upkeep 2   Range 1  Vision 2  Upkeep 1   Range 3  Vision 2  Upkeep 2

Order: MOVE → (11,28) → (12,27) → (13,27)
```

Stats are the SPEC §5.3 table. **Range** is weapon/attack range (inf/armor 1,
artillery 3) and is distinct from **Vision** (2 for all units, SPEC §9.3) — the
draft conflated them. The current-order line spells out the exact planned path.

---

## 7. Terrain / hex inspector panel

Describes the hovered (or selected) hex. Values are SPEC §4.2 / §3.

```text
WOODS  (15,22)              CITY  (09,31)              BRIDGE  (24,20)
Defense +2                  Defense +2                 Defense +0
Move cost 1                 Muster +3                  Passable river crossing
Blocks LOS: yes            Repair +2 (resting)        Road: yes · chokepoint
Road: no                   Recruit: yes
Control: Blue              Control: Blue
```

Terrain defense (stationary defenders only, SPEC §4.2): Plains 0 · Woods +2 ·
Hills **+1** · Town +2 · City +2 · Bridge 0. Only **Woods blocks LOS** in v0.1
(SPEC §9.2); hills do **not** block LOS (the draft implied hills affect
visibility — they don't yet). Rivers are impassable except bridge hexes (SPEC
§3.4). Road is a boolean layer granting +1 movement only if the unit's *entire*
planned path is on connected road (SPEC §3.3). Terrain matters because it changes
combat and planning decisions — keep this panel honest.

---

## 8. Order palette

Large, icon-first buttons, gated by the selected unit's type. The set mirrors
the engine order union; the current implementation already renders this for the
selected unit (`GameShell.tsx` `OrderActions`).

```text
Infantry / Armor:   [ Move ]  [ Rest ]  [ Hold ]  [ Clear ]
Artillery:          [ Move ]  [ Barrage ]  [ Rest ]  [ Hold ]  [ Clear ]
No unit selected:   [ Select a unit ]
```

- **No explicit Attack button.** "Attack" is what a `Move` order *becomes* when
  its path enters an enemy-occupied hex — surface it as the `ATTACK` label and a
  red contact marker on the destination, not as a separate button/order.
- **Artillery has no Move-to-attack** and no direct Attack: it either Moves *or*
  Barrages (never both in a turn, SPEC §5.4/§7.2). Barrage targets a *hex* within
  range 3, resolves before movement.
- **Rest** repairs +1 (cap 7) anywhere, or +2 (cap 10) on a town/city, but only
  if the unit takes no damage that turn (SPEC §7.3). **Hold** gives +1 defense if
  attacked, no repair (SPEC §7.4).
- **Clear** removes the staged order (unit returns to idle).

Button states: available (normal) · active mode (bright outline) · disabled
(darkened, e.g. Barrage on non-artillery) · invalid target (red flash) · has
pending order (small check). **No CP cost badges** — there are no command points.

---

## 9. Map interactions

| Action                                       | Result                                   |
| -------------------------------------------- | ---------------------------------------- |
| Click own unit                               | Select it                                |
| Click empty hex with Move active             | Set/extend the exact path to it          |
| Drag along hexes                             | Hand-plot an exact path                  |
| Click an enemy-occupied hex (Move active)    | Plot a contact/attack step (infantry/armor) |
| Click target hex (Barrage active)            | Set barrage target (artillery, range 3)  |
| Right-click                                  | Cancel current targeting mode            |
| Hover hex                                     | Terrain inspector                        |
| Hover visible enemy                          | Known-unit info (only if in your vision) |
| Space                                        | Select next unordered unit               |
| Enter                                        | Lock orders (with confirm if warnings)   |
| Esc                                          | Cancel mode / close panel                |

Pan/zoom with integer scaling for crisp pixels (already in `Board.tsx`); per-hex
hit-testing turns clicks into `(q, r)`. There is **no stacking** — paths may not
end on, or route through, a hex held by another unit (SPEC §8.1).

**Auto-camera.** The player should never open the game zoomed out on empty terrain
or hunting for their force:

- *On load*, the camera frames the viewer's own units: it centres their bounding
  box in the map rectangle and zooms in as far as keeps them all comfortably in
  frame (a padding margin on every side, capped at a sane magnification so a lone
  or tightly-clustered force doesn't fill the screen). With no own units to frame
  (spectator/replay) it falls back to fitting the whole map.
- *On roster select* (§5.1), the camera only moves when the unit isn't already
  comfortably in frame — defined as sitting within the map rectangle inset by a
  margin on each side. A unit nearer the edge glides to centre at the current zoom
  over a short ease; a unit already well inside the view stays put. Any manual pan
  or zoom immediately cancels an in-flight glide.
- *Panning is bounded*: the camera may scroll at most ~one hex radius past the
  map's outer edge, so the player can nudge edge hexes clear of the docked panels
  without drifting into open background. This bound applies to manual pan/zoom, to
  reveal glides, and to the opening fit; a map smaller than its rectangle is
  centred rather than free to slide around.

---

## 10. Movement & planning visuals (Pixi overlays)

Because simultaneous movement is the game's gimmick, planned-path readability is
not optional. When a unit is selected, draw above the tiles:

| Element                | Visual                                     |
| ---------------------- | ------------------------------------------ |
| Legal move range       | subtle tint / outline of reachable hexes   |
| Planned path           | arrow chain along the exact hexes          |
| Movement pulses        | subtle per-step numbering (1..N)           |
| Final hex              | bright outline                             |
| Attack / contact hex   | red/orange crossed marker                  |
| ZOC-impeded path       | path churns red from the blocked hex on    |
| Traffic risk (own)     | amber warning marker on the contested hex  |
| Barrage target & range | distinct red/orange ring + target marker   |

Reachability respects road bonus, impassability, stacking, and ZOC stop-on-entry
(see `planning/reach.ts`). Enemy-ZOC guidance must be plain-language (SPEC §16.1),
e.g. surfaced near the unit or in the inspector:

> This unit is in enemy ZOC. It may attack, hold, or withdraw — it cannot move
> along the enemy line.

---

## 11. Lock control (not "End Turn")

Bottom-right block. This is a **readiness indicator**, not just a button — and in
this async game it **locks your orders**; resolution happens server-side once both
players lock (`POST /games/{id}/orders`, then poll). Do not label it "End Turn"
or imply it resolves the turn immediately.

```text
┌──────────────┐
│ LOCK ORDERS  │
│ 14 / 18 set  │
│ ⚠ 3 idle     │
└──────────────┘
```

States:

| Condition                       | Button                                 |
| ------------------------------- | -------------------------------------- |
| All units ordered, all valid    | `LOCK ORDERS`                          |
| Some idle (still lockable)      | `LOCK ORDERS` + idle warning           |
| Any invalid staged order        | `FIX ORDERS` (disabled until resolved) |
| Already locked                  | `LOCKED — waiting on opponent…`        |

Idle units are allowed (an unordered unit simply holds position) — so idle is a
warning, not a block. Locking with warnings opens a confirm that can cycle the
issues first:

```text
3 units have no orders.
2 of your paths risk a traffic jam.

Lock anyway?
[ Review issues ]   [ Lock orders ]
```

There is **no "Over Command" state** (no command-point budget to exceed).

---

## 12. Minimap panel

Information-dense, not pretty. Small, corner-docked. Shows: terrain
background, your visible area, your units, **currently-visible** enemy units
(hidden enemies are simply absent — fog is server-enforced, SPEC §9 / ROADMAP P6),
towns/cities with control color, the selected unit, the viewport rectangle, and
optionally planned paths as faint lines. Controls: zoom `+ / −` and a layer
toggle. Default layers: **Control + Units**. Optional layers: Terrain · Orders ·
ZOC.

The minimap is a nice-to-have. A clean order workflow (roster + palette + selected
detail + path visuals) is life support — build those first.

---

## 13. Replay panel (Phase 10)

After a turn resolves, the board enters replay over the post-turn state. Step
through the event log from `GET /games/{id}/turns/{turn}` (the engine emits a
discriminated `Event` log — moves, blocks, traffic jams, ZOC stops, combat,
captures, repairs, economy). Provide a scrubber / step controls and an event list
with the SPEC §16.3 plain-language combat explanations, e.g.:

```text
Blue Armor attacked Red Infantry in Woods.
Red Infantry received +2 terrain defense.
Blue Armor dealt 2 damage · Red Infantry dealt 1 damage.
Red Infantry was destroyed. Blue Armor entered the hex and continued moving.
```

Replay obeys fog: you only see events you could witness (own units, or things on
hexes you can see).

---

## 14. Visual style & typography

Dark steel/charcoal frame with faction accents; avoid large fields of pure black
(it makes the map look pasted into a void).

```text
Panels:        dark charcoal / blue-black (docked Pixi chrome, opaque)
Borders:       muted steel
Text:          warm off-white; secondary text desaturated gray
Blue faction:  saturated military blue
Red faction:   brick / crimson
Warning:       amber       Invalid: red       Valid: pale blue / green-blue
```

Two type roles: a condensed sans for labels; a monospace for numbers/stats so
coordinates, HP, and order labels align in the roster. Faction color comes from
Pixi-tinting one base unit sprite (not per-color art); damage is an overlay
(ROADMAP decision 7).

---

## 15. Build order

The functional planning loop matters far more than the minimap. Suggested order,
mapped to the roadmap:

1. Thin header: turn / phase / muster / orders / warnings (Phase 8–9)
2. Roster panel (Units tab) with selectable rows + "needs orders" sort (P9)
3. Selected-unit detail panel (P9)
4. Order palette (P9 — partly built in `GameShell.tsx`)
5. Movement/path/range/ZOC Pixi overlays (P9 — partly built)
6. Terrain/hex inspector (P9)
7. Lock control + warning review (P9)
8. Minimap (P9/P10)
9. Control tab (P9/P10)
10. Replay panel (P10)

---

## 16. Corrections from the source draft

Changes made while reconciling the external draft against `SPEC.md`:

- **Removed Command Points entirely.** The draft proposed a CP planning budget,
  per-order CP costs, a top-bar `Command N/M` field, and an "Over Command" lock
  state. None exist in v0.1 — the only economy is **Muster** (SPEC §6). CP is
  noted only as an explicitly out-of-scope idea.
- **No Attack order/button.** Kept "Attack" only as a derived *label* on a Move
  that enters enemy contact (SPEC §7); removed any standalone Attack button.
  Clarified artillery can never attack-by-move (SPEC §5.4).
- **Lock, not End Turn.** Reframed the end-of-turn control for the async
  two-player model: locking submits orders; the server resolves when both lock.
  Removed the single-player "end turn → resolve now" framing.
- **Coordinates are `(q, r)` axial**, not `(x, y)`.
- **Units have no names.** Replaced "M4 Sherman" / "1st Infantry" flavor with
  type-based identity (SPEC §5.1).
- **Range vs Vision separated.** Range = weapon range (1/1/3); Vision = 2 for all
  (SPEC §9.3) — the draft conflated them.
- **Terrain corrected to SPEC §4.2:** Hills are +1 defense and do **not** block
  LOS; only Woods blocks LOS. Bridge +0. Rest/Hold effects pinned to the SPEC
  values (Rest +1/cap7 or +2/cap10; Hold +1 defense).
- **Recruitment is at friendly *cities* only** (SPEC §6.5), not any settlement.
- **Layout: thin React header over one all-Pixi surface.** The draft's left +
  bottom panels are kept and *moved into Pixi* — only the very top is React. The
  panels dock and may shrink the map (acceptable for now); a fullscreen-map toggle
  is a later, non-blocking addition.
- **Added game-screen lifecycle states** (lobby / your-move / waiting / replay /
  game-over) the draft omitted, plus a Replay panel section for Phase 10.
- **Kept and endorsed** the draft's strongest idea: organize the entire planning
  UI around **"which units still need orders?"**
