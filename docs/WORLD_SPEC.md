# World Slice 1 Spec: the open world

The first concrete step toward [`VISION.md`](./VISION.md): a persistent world with
many commanders on a large generated map, resolving on a real two-a-day clock,
with packed **columns** for long-distance transit and multi-turn **travel
orders**. Tactical rules for deployed units are unchanged from
[`SPEC.md`](./SPEC.md), which stays authoritative for them.

Status: **draft for user review** (2026-11-08; revised the same day after the sizing
study, VISION §12). Numbers are starting defaults to be tuned with the world simulator
(§9); bracketed **[tunable]** marks the ones we expect to move.

**Direction (user, 2026-11-08): no 2-player detour.** We build and test against the
world, not against Twin Rivers. The engine stays one engine — a 2-player 40×40 game
is just a tiny world — but new work is judged by how it plays at world scale.

---

## 1. Scope

In slice 1:

- One persistent **world**: sized for 200 player slots (VISION §1), with roughly 100
  filled at first (the user plus bots), on a generated map, with no season end.
- **Turn clock:** two scheduled resolutions a day (§2).
- **Postures:** deployed vs column, with auto-deploy on contact (§4).
- **Travel orders:** multi-turn "go there" for columns (§5).
- **World generator:** terrain, rivers, roads, settlements, provinces, start regions (§6).
- **Command cap that grows with holdings** (§7).
- **Bots that play a world**, plus a headless **world simulator** with metrics (§8–9).
- Persistence, the scheduler, and a frontend world view (§10–11).

Deferred to later slices (see VISION §10): resource sites and unit unlocks (slice 2),
unrest/marauders (slice 3), factions, diplomacy, chat, NPC governments, LLM
commanders, the season arc. Also deferred: human doctrine/standing behaviors
(VISION §4), supply, rail, joining a world mid-run, other time zones.

## 2. Turn clock

- A world resolves at **01:00 and 13:00 America/New_York** (DST-aware). [tunable per world]
- Resolution happens on schedule whether or not players have locked in. Until
  the deadline, a human may edit and re-lock orders.
- **A unit with no fresh order** continues its standing travel order (§5) if it has
  one. Otherwise it does nothing: a deployed unit behaves as v0.1 with no order,
  and a column stays put.
- Bots plan at resolution time against their fog-redacted view.
- Dev and sim worlds use a **manual clock** (resolve on command), so the sim can run
  as fast as compute allows.

## 3. Hostility

Every rule that says "enemy" goes through one predicate,
`hostile(a: PlayerId, b: PlayerId) -> bool`. In slice 1 every pair of distinct
players is hostile (warlord free-for-all). Factions and diplomacy (later slices)
change only this predicate's data, not the rules that call it.

## 4. Postures: deployed and column

Each unit has a `posture`: `deployed` (the default, and today's behavior) or `column`.

### 4.1 Deployed

Exactly SPEC v0.1: one deployed unit per hex, exerts and obeys ZOC, Move / Barrage /
Hold / Rest, deployed movement rates (infantry 2, armor 4, artillery 1).

### 4.2 Column

| Property | Column rule |
| --- | --- |
| Movement | Road step costs 1 pulse, off-road step costs 2 [tunable] |
| Stacking | Up to 6 friendly columns per hex [tunable], plus at most one friendly deployed unit. A column that would enter a full hex waits. Where columns may travel (anywhere vs roads only vs stack only on roads) is open: VISION §5 |
| ZOC | Exerts none; may not enter a hex in hostile ZOC or containing a hostile unit |
| Attack | Cannot attack |
| Defense | Defense −3, no terrain or Hold bonus [tunable] |
| Capture | Cannot capture |
| Vision | As deployed |
| Repair | Rests like a deployed unit (+2 in a friendly settlement, so garrisons stack and heal) |

### 4.3 Turn pulses

A world turn has **16 movement pulses** [tunable], up from v0.1's max of 4.
Deployed units act in pulses 1…their move (as today), so all deployed maneuver and
melee happens in the opening tactical window. Columns spend pulses as they step.
Barrage still resolves before pulse 1.

### 4.4 Packing (deployed → column)

- Ordered with a **Travel** order given to a deployed unit (§5), or an explicit
  **Pack** order.
- A unit that **starts the turn in hostile ZOC cannot pack**. Disengage first, with
  a v0.1 withdrawal; you can't escape a fight by packing.
- Packing occupies **pulses 1–4**. The unit is stationary and still deployed while
  packing, and becomes a column from pulse 5. That leaves 12 pulses of column
  movement on the first travel turn (12 road hexes or 6 off-road).

### 4.5 Deploying (column → deployed)

- **Ordered deploy:** a travel order with `on_arrival: deploy`, or an explicit
  **Deploy** order. The unit deploys clean at the end of the turn in which it stops.
- **Auto-deploy on contact:** at the start of each pulse, a column halts and
  deploys if any of these hold:
  1. its next step would come within **X = 3** hexes of a hostile unit [tunable]. Distance
     uses true positions, not the owner's fog view, so a column can blunder into an enemy
     hidden in woods. The sizing study measured each extra hex of X at ~1.5 points of front
     share; 3 leaves a hex of buffer outside hostile ZOC;
  2. a hostile unit comes within X of it (it was approached);
  3. it was attacked or barraged this turn (it deploys after that combat, if it survives).

  An auto-deployed unit is **disordered** for the following turn's execution:
  defense −2, cannot attack [tunable]. This is the cost of arriving blind; scouting
  ahead avoids it.
- **Placement:** a deploying column takes its current hex if no friendly deployed
  unit is there. Otherwise it spills to the nearest free hex within radius 2 that
  is not in hostile ZOC. Ties break by backtracking along its route, then by axial
  order (deterministic). If no hex is free, it **stays a column** and stays
  vulnerable. That is the built-in penalty for bunching up.
- Deploying ends the unit's movement for the turn, and ends its travel order.

### 4.6 Combat against columns

- **Barrage** hits *every* unit in the target hex: the deployed occupant with its
  terrain/Hold bonus (less any disorder), and each column at −3 defense with no
  terrain or Hold bonus. Shelling a stacked road is brutal. Packed survivors deploy,
  disordered, before the first pulse.
- **Melee:** because X ≥ 1 and contact is checked at the start of every pulse, a
  column is never adjacent to a hostile unit when attacks are declared; it has
  already deployed (disordered). Melee therefore always meets a deployed defender,
  and the disorder penalty is the ambush cost. The one exception is a column stuck
  with no room to deploy: attacks into its hex are blocked (`column_sheltered`).
  *(Implemented in W1; this replaces the draft's per-column melee rule.)*
- "All opposition destroyed" (SPEC §12.5) means every unit in the hex, so an
  attacker cannot enter a hex that still holds hostile columns.
- Hostile columns never fight each other directly; contact auto-deploys both.

### 4.7 Relief in place and friendly swaps

This changes SPEC §8.2, where two friendly units trying to swap hexes jam.

- **Friendly swaps:** two friendly deployed units that step into each other's hex in the
  same pulse swap instead of jamming; both keep moving along their paths. A closed
  rotation of three or more resolves the same way. A third unit contending for one of
  the hexes turns it back into a jam.
- **Relief in place** needs no extra order: the fresh unit moves into the line hex and the
  relieved unit moves out through the fresh unit's hex, in the same pulse. ZOC still
  applies to each step (an engaged unit can't step into another hostile-ZOC hex).
  *(Implemented with W1.)*
- Why: rotating a front line without this left ~14% of front slots reachable only through
  occupied slots, and clusters of slots locked for 50+ turns. Relief swaps added ~2 points
  of front share and cut line jams by two-thirds (VISION §12).

## 5. Travel orders (multi-turn)

```
TravelOrder { unit_id, destination: Axial, via: list[Axial] (≤ 4 waypoints),
              on_arrival: "deployed" | "column" }
```

- **Valid for** a column, or a deployed unit that is not starting in hostile ZOC
  (it packs first, §4.4; an engaged unit's order ends with reason `engaged`).
  Deployed units keep using v0.1 exact-path Move orders for tactical maneuver.
- **Routing** (`app/engine/routing.py`): deterministic A* over the *static* map,
  using column step costs, with a fixed tie-break. Each waypoint leg is planned
  separately. Routes ignore units entirely: they are fog-honest (terrain is public)
  and identical for every observer. Reachability is checked when the order is
  validated, at the API boundary.
- **Standing** (`app/engine/travel.py`): the route is computed once, when the order is
  issued, and stored in `GameState.standing_orders` (keyed by unit). Each turn the
  unit has no fresh order, the engine marches it along the rest of the route. The
  order ends, with a `TravelEnded{reason}` event, on:
  - `arrived`: the unit entered the last hex (and deploys clean unless `on_arrival`
    is `column`);
  - `contact`: auto-deploy, a column stuck without room, or packing interrupted;
  - `engaged`: it began the turn in hostile ZOC and could not pack;
  - `replaced`: a fresh order of any kind (Hold or Deploy stop a column; a new
    Travel reroutes);
  - `destroyed`.
- **Private intent:** fog redaction drops other players' standing orders, and
  `TravelEnded` is visible only to the owner. Others see the column move.
- The planner shows the route and an **ETA in turns** (`travel_eta`, given clean
  roads; it counts the packing turn and the credit a turn can't spend).
- Friendly columns never jam each other, because they stack.
- Perf: a cross-world route on a roadless 350² map takes ~1 s; a typical
  50–150-hex route, 0.02–0.3 s. Validation and the engine each route once. Bots
  issuing many long orders per turn may need a shared distance field (W4).

## 6. World generator

`app/world/generate.py`: seeded, deterministic, pure.

- **Size:** 200 slots on about **350×350** (~122k hexes; ~610 hexes per slot when full,
  ~1,200 per live player at 100 live) [tunable]. Flank room levels off past ~600–900
  hexes per player; a bigger map only lengthens marches (VISION §6, §12). Small worlds
  (e.g. 12 slots on 110×110) stay supported for tests.
- **Unfilled slots:** their start regions are generated but left neutral; that land is
  room for relocation and new players (VISION §7).
- **Terrain:** plains, woods, hills (as v0.1); rivers as impassable hex chains;
  bridges where roads cross them.
- **Settlements:** about 1 per 70 hexes [tunable], roughly 3× v0.1's density, mostly
  towns. Cities are rarer and anchor regions.
- **Roads:** a spanning network linking settlements plus redundant links, so no
  single bridge decides a whole region. Each territory's roads must form one connected
  network, and roads must reach its borders. In the study, 52–60% of territories had
  split road networks, which stranded brigades under roads-only movement.
- **Provinces:** every settlement anchors a province (about 20–40 hexes), assigned
  by travel-cost distance. Province control is derived from its anchor settlement,
  with no extra state. Provinces exist for borders and the strategic view.
- **Start regions:** capitals spaced by Poisson-disc sampling. Each player starts
  with one city and 4 units (2 infantry, 1 armor, 1 artillery) [tunable].
- **Fairness gates** (generator tests): every settlement is road-reachable; every
  start has a similar settlement count and road access within radius R; no start
  is within X hexes of another.
- **Review:** the generator emits a PNG preview (terrain, roads, provinces, starts)
  for map-design review. Hex tile art is unchanged; this is a debug render, not board art.

**As built (W3):** `app/world/generate.py` (pure Python, no numpy; a 200-slot 349² world
takes ~18 s), `app/world/preview.py` (stdlib PNG), gates in `tests/world/`.

- *Territories* (start regions) are each capital's nearest land in steps over passable
  hexes, so roads never move a border. Fix-ups lay roads only inside territories: one road
  network per territory, a road across every border with ≥ 4 hex contacts, and new towns
  for any territory below half the median settlement count.
- Capitals: farthest-point sampling, 6 Lloyd steps on open hexes, then each seats on a
  nearby settlement (cities preferred) at least 60% of the ideal lattice spacing from
  the others. Provinces average ~70 hexes at the default density, not 20–40.
- `fill_order` orders slots so any prefix is spread out; `to_state(players)` seats the
  first N, each with a garrisoned capital and the other three units beside it.
- Known spread (seed 1, 200 slots): territory size 180–1,080 hexes (middle half
  517–681); settlements within 14 hexes of a capital 4–12 (median 8). Edge and river
  starts run small. The W4 sim will show whether it matters.

## 7. Economy and command cap

- Muster as v0.1 (town +2, city +3, upkeep). The bank cap scales to 40 [tunable].
- **Command cap** replaces the flat 20-unit cap:
  `cap = 4 + 1 per town + 2 per city`, up to a maximum of **15** [tunable]. A mature
  player fields a division. Growth comes from land, not grinding.
- Recruits deploy at friendly cities (as v0.1).
- **Healing:** resting in a friendly settlement restores **3 HP/turn** [tunable], up from
  v0.1's 2. Expect about a third of an army in the line at once, not half (VISION §7).
- **Elimination:** a player with no settlements and no units is out of the world.
  A player with units but no cities can't recruit, but can retake ground.
- No victory condition in slice 1; the world simply runs.
- *As built:* `GameConfig.command_cap` (`CommandCap`), `muster_bank_cap = 40` and
  `settlement_repair = 3` are per-game config that `GeneratedWorld.to_state` sets;
  v0.1 games keep their flat cap of 20, bank cap of 20 and +2 healing. The bank
  ceiling is now checked on `GameState` rather than on `Player`.

## 8. Bots

- `Policy.plan` takes an N-player redacted view. The §4.4 Hold opponent model
  generalizes to "every visible hostile unit Holds."
- **Strategic layer:** scores objectives world-wide with distance discounting.
  Assigns distant units **Travel** orders; units near contact get tactical
  candidates (AI_SPEC §4.5) with search radius bounded around each unit.
- **Personalities:** per-bot seeded weights (aggression, expansion appetite, garrison
  share), so twelve bots aren't twelve clones.
- **Budget:** ≤ 2 s per bot-turn at the default world size (today's search bot is
  ~0.5 s on 40×40). 100 bots are about 3 minutes per turn on one core, which is fine
  inside a 12-hour window and parallelizes for the simulator.
- **Plan interface (for the later LLM strategist, VISION §9):** each bot turn runs
  `tactical_policy(view, args)`, where a *strategist* picks the policy and its arguments
  (target region, reserve share, posture). In slice 1 the strategist is a scripted stand-in.
  Defining the seam now means the LLM layer drops in later without touching the tactical
  AI.
- AI_SPEC gets a world addendum when this phase starts.

## 9. World simulator

`uv run python -m app.world.sim --seed S --slots 12 --turns 60 [--json out]`

Generates a world, fills every slot with bots, and resolves on the manual clock.
It reports:

- **Transit:** friendly jams (split by posture and distance from contact); travel
  orders ended by arrival / contact / replacement; stuck-in-column count.
- **Contact:** turn of first contact per player pair; auto-deploys; disordered fights.
- **Fronts:** deployed units in hostile contact per turn; engagement size
  (units per side).
- **Expansion:** settlements and command cap per player over time; eliminations.
- **Perf:** bot think time and resolution time per turn.
- **Determinism:** two runs on the same seed must be identical (a CI test).

A sim world can be **loaded into local dev to spectate and replay**. This is the
main way to judge whether the world is fun before a human plays it.

**Slice 1 acceptance gates** (over 20 seeds):

1. **Jams:** column jams near zero (the stack cap rarely binds); deployed jams occur
   only near contact.
2. **Travel:** ≥ 95% of travel orders end in arrival or contact, none in limbo.
3. **Expansion:** every bot captures ≥ 3 settlements by turn 20, and all pairs of
   neighboring bots have made contact by turn 30 [tunable].
4. **Fronts:** median engagement has ≥ 3 deployed units per side.
5. **Determinism and perf:** whole-world resolution < 5 s; a 60-turn sim < 30 min
   on a laptop.

The real gate is the user playing a live world on the real clock for a week.

## 10. Engine, persistence, clock

- **One engine, generalized.** Posture defaults to `deployed`, so the v0.1 rules
  and tests stay green. Columns and travel are additive.
- **Static/dynamic split:** the generated map is stored once per world. Dynamic
  state is units, settlements, players, `standing_orders` and the turn. `turns.state_after`
  stops embedding the map.
- **Indexed lookups:** `hex_at`/`unit_at` are linear scans today, which won't
  survive 12k hexes; index them, then measure before any deeper optimization.
- **Schema:** generalize the existing `games` / `players` / `turn_orders` / `turns`
  tables to N slots plus a world clock and a static map, rather than forking parallel
  world tables. New tables need the `service_role` grant (CLAUDE.md gotcha).
- **Scheduler:** a Cloud Scheduler job calls an OIDC-authenticated internal resolve
  endpoint at each tick. It is idempotent on `(game_id, turn)`. Provisioning it is
  an infra/IAM change, so confirm before applying.
- Whole-world resolution in one call is fine at 12 slots (~180 units). Splitting
  resolution into independent contact clusters waits until measurements demand it.

### 10.1 W5 design (drafted 2026-11-09; map storage built, the rest proposed)

Sizes measured on a 200-slot world: the full `GameState` JSON is ~7 MB, almost all of
it map. Embedding that in every `games.state` and `turns.state_after` row would cost
~14 MB per turn.

- **`game_maps` table ✅ (built 2026-11-09):** `id`, `width`, `height`, generator `params`
  (jsonb, so a map can be regenerated), and `terrain`/`roads` in the compact grid
  encoding (`app.engine.map_codec.CompactMap`: one char per hex each, row-major over the
  odd-q rectangle, the scenario alphabet). `games.map_id` points at it; a game with a
  `map_id` stores `games.state` and `turns.state_after` without the `map` key, and
  `SupabaseGameRepo` rejoins the map on load from a per-process LRU (`MapCache`), so
  every state of a world shares one map object. Measured on a 200-slot world in local
  Postgres: the map row is 40 KB and a 100-player state row 20 KB after TOAST
  compression (versus 8.4 MB of JSON for a full state). Decoding the map costs ~1.2 s on
  a cold process, then a load is ~0.15 s.
- **`games`** gains `kind` (`match` | `world`), `clock` (`manual` | `twice_daily`) and `next_tick_at`. For a world, `games.state` and
  `turns.state_after` hold the state *without* its map. The repo rejoins the cached map
  on load, so the engine still sees one `GameState`.
- **Slots ✅ (built 2026-11-09):** `game_maps.slots` holds the capitals in fill order,
  and fill position k is player `s<k+1>` (`s001`, `s002`, …). `GameService.create_world`
  generates the world, stores the map, seats the bots (`bot_policy = "world"`) and
  starts the game active. `join_world` seats a human in the first open slot
  (`app.world.seating`): never held, capital still neutral, and no unit within 3 hexes
  of it. The `players` row is written first, so its `(game_id, slot)` uniqueness
  settles two people joining at once.
- **Tick (service ✅ 2026-11-09, routes and scheduler to come):** `tick_world` plans
  every bot from its fogged view (personalities seeded from the game id), adds the
  humans' stored submissions, and resolves. A world submission is stored but never
  resolves the turn itself. Measured on local Supabase with 200 slots, 100 bots and
  one human: ~1.1 s per tick, 1.8 s on a cold process. Still open: optimistic
  concurrency on `games.state`, so a join landing during a tick can't be lost.
  Planned route: `POST /internal/worlds/{id}/tick`, authenticated by OIDC from a Cloud
  Scheduler service account. Bots plan, missing human orders become "no order" (standing
  travel continues), the turn resolves, and the `(game_id, turn)` unique constraint on
  `turns` makes a retried tick a no-op. Dev worlds use `POST /dev/worlds/{id}/resolve`.
- **Routes ✅ (built 2026-11-09, `app/games/world_routes.py`):**
  - `POST /worlds/{id}/join` takes a slot.
  - `GET /worlds/{id}` returns the fogged `WorldView`, whose `WorldState` is
    `GameState` minus the map.
  - `GET /worlds/{id}/map` returns the `CompactMap` with `Cache-Control: immutable`.
  - `GET /worlds/{id}/turns/{n}` returns the fogged replay.
  - Dev-only: `POST /dev/worlds` creates a world and `POST /dev/worlds/{id}/tick`
    is the manual clock.

  Orders still go to `POST /games/{id}/orders` and are stored until the tick. The
  2-player `/games` flow stays until the W6 frontend switches to worlds, then goes.
- **Infra** (confirm before applying): a scheduler job per world, at 01:00 and 13:00
  America/New_York, and an invoker binding for the scheduler's service account.

## 11. Frontend world view

- **Camera:** pan/zoom over a world-size board with chunked, culled rendering, plus
  a minimap.
- **Strategic zoom:** at far zoom, show province borders, ownership tint, and
  unit/stack tokens instead of tiles.
- **Columns:** a distinct column token with a stack count. Disordered marker.
- **Planning:**
  - click a destination to issue Travel, with a route preview and ETA;
  - Move, Hold, Rest and Barrage as today, plus Pack and Deploy;
  - a countdown to the next tick.
- **Replay:** a world turn filtered to visible events, with a list of "your
  engagements" to jump between.
- **Zoom tiers:** this slice builds the operational and tactical tiers. The full-screen
  theater view, fog-aware with stale intel, comes with alliances (VISION §10).
- **Art:** column tokens, the province border style, and strategic-zoom tokens are
  new art, generated with Codex (VISION §11). They go through Artifact review for the
  user's pick before they're applied.

## 12. Implementation phases

| Phase | Delivers | Gate |
| --- | --- | --- |
| **W0** Engine generalization ✅ | indexed lookups, shared static map, `hostile()`, perf baseline (`app.world.bench`). The static/dynamic *storage* split and N-slot turn flow moved to W5, where persistence changes | v0.1 suite green; 350² / 1,500 units resolves in 0.05 s, one player's fog in 0.006 s |
| **W1** Columns ✅ | posture, March/Deploy orders, pack/deploy, column movement + stacking, auto-deploy + placement, disorder, barrage vs stacks, §4.7 friendly swaps and rotations | `tests/engine/test_columns.py` |
| **W2** Travel ✅ | `TravelOrder`, routing, standing orders in state, validation, `TravelEnded` | pytest; multi-turn arrival/halt cases |
| **W3** World generator ✅ (review pending) | §6, fairness gates, PNG preview | user reviews sample worlds |
| **W4** Bots + sim (first cut) | §8–9: `app/ai/world.py`, `app/world/sim.py`, AI_SPEC §12 | §9 acceptance gates (not yet met: see AI_SPEC §12.4) |
| **W5** Persistence + clock — started | schema (✅ `game_maps` + `games.map_id`, map stored once), scheduled resolve, dev manual clock | live world ticks on schedule |
| **W6** Frontend world view | §11 | art ratified; user can plan a world turn |
| **W7** Live playtest | the user plays a week | the user's verdict |

W0–W4 are headless and can run largely in parallel with W5–W6 once W0 is done.

## 13. Open questions

1. Is packing taking pulses 1–4 (vs a whole turn) the right cost?
2. Where columns may travel: anywhere (current text), roads only, or anywhere but
   stacking only on roads and in towns (untested). See VISION §5.
3. At 350² the median capital-to-capital march is ~8–9 days. Strategic movement
   (rail or similar) is planned for slice 2. Should a minimal version come into slice 1?
4. The first live world: full 200-slot map with ~100 bots, or a smaller rehearsal world
   first?
