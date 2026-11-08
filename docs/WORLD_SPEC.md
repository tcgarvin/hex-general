# World Slice 1 Spec: the open world

The first concrete step toward [`VISION.md`](./VISION.md): a persistent world with
many commanders on a large generated map, resolving on a real two-a-day clock,
with packed **columns** for long-distance transit and multi-turn **travel
orders**. Tactical rules for deployed units are unchanged from
[`SPEC.md`](./SPEC.md), which stays authoritative for them.

Status: **draft for user review** (2026-11-08). Numbers are starting defaults to be
tuned with the world simulator (§9); bracketed **[tunable]** marks the ones we expect
to move.

**Direction (user, 2026-11-08): no 2-player detour.** We build and test against the
world, not against Twin Rivers. The engine stays one engine — a 2-player 40×40 game
is just a tiny world — but new work is judged by how it plays at world scale.

---

## 1. Scope

In slice 1:

- One persistent **world**: N player slots (1 human + bots), generated map, no
  season end.
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
| Stacking | Any number of friendly columns per hex, plus at most one friendly deployed unit |
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
  1. it is adjacent to a hostile unit;
  2. its next step would enter hostile ZOC or a hostile-occupied hex;
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

- **Barrage** hits *every* unit in the target hex. Shelling a stacked road is brutal.
- **Melee** into a hex: if a deployed unit is there, it is the defender and the
  columns behind it are sheltered. If only columns are there, each column defends
  separately; the attacker deals its damage to each of them and receives each
  one's (reduced) return damage.
- "All opposition destroyed" (SPEC §12.5) means every unit in the hex.
- Hostile columns never fight each other directly; contact auto-deploys both.

## 5. Travel orders (multi-turn)

```
TravelOrder { unit_id, destination: Axial, via: list[Axial] (≤ 4 waypoints),
              on_arrival: "deploy" | "column" }
```

- **Valid for** a column, or a deployed unit that is not starting in hostile ZOC
  (it packs first, §4.4). Deployed units keep using v0.1 exact-path Move orders for
  tactical maneuver. No multi-turn orders for deployed units.
- **Routing:** deterministic A* over the *static* map, using column step costs, with
  a fixed tie-break. Routes ignore units entirely: they are fog-honest (terrain is
  public) and identical for every observer. Reachability is checked when the order
  is validated, at the API boundary.
- **Standing:** the order is stored in world state (`standing_orders`, keyed by unit)
  and continues every turn until one of these happens:
  - arrival;
  - contact auto-deploy (event `TravelHalted{reason}`);
  - an explicit deploy;
  - it is replaced by a fresh order.
- The planner shows the route and an **ETA in turns** (given clean roads).
- Friendly columns never jam each other, because they stack.

## 6. World generator

`app/world/generate.py`: seeded, deterministic, pure.

- **Size:** default 12 slots on about 110×110 (~1,000 hexes per player, close to
  v0.1's 800) [tunable].
- **Terrain:** plains, woods, hills (as v0.1); rivers as impassable hex chains;
  bridges where roads cross them.
- **Settlements:** about 1 per 70 hexes [tunable], roughly 3× v0.1's density, mostly
  towns. Cities are rarer and anchor regions.
- **Roads:** a spanning network linking settlements plus redundant links, so no
  single bridge decides a whole region.
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

## 7. Economy and command cap

- Muster as v0.1 (town +2, city +3, upkeep). The bank cap scales to 40 [tunable].
- **Command cap** replaces the flat 20-unit cap:
  `cap = 4 + 1 per town + 2 per city`, up to a maximum of **15** [tunable]. A mature
  player fields a division. Growth comes from land, not grinding.
- Recruits deploy at friendly cities (as v0.1).
- **Elimination:** a player with no settlements and no units is out of the world.
  A player with units but no cities can't recruit, but can retake ground.
- No victory condition in slice 1; the world simply runs.

## 8. Bots

- `Policy.plan` takes an N-player redacted view. The §4.4 Hold opponent model
  generalizes to "every visible hostile unit Holds."
- **Strategic layer:** scores objectives world-wide with distance discounting.
  Assigns distant units **Travel** orders; units near contact get tactical
  candidates (AI_SPEC §4.5) with search radius bounded around each unit.
- **Personalities:** per-bot seeded weights (aggression, expansion appetite, garrison
  share), so twelve bots aren't twelve clones.
- **Budget:** ≤ 2 s per bot-turn at the default world size (today's search bot is
  ~0.5 s on 40×40).
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

1. **Jams:** zero column-involved jams; deployed jams occur only near contact.
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
- **Art:** column tokens, the province border style, and strategic-zoom tokens are
  new art. They go through Artifact review for the user's pick before they're applied.

## 12. Implementation phases

| Phase | Delivers | Gate |
| --- | --- | --- |
| **W0** Engine generalization | static/dynamic split, indexed lookups, `hostile()`, N-player turn flow, perf baseline | v0.1 suite green; perf numbers recorded |
| **W1** Columns | posture, pack/deploy, column movement + stacking, auto-deploy + placement, disorder, column combat | pytest rule coverage for §4 |
| **W2** Travel | `TravelOrder`, routing, standing orders in state, validation, `TravelHalted` | pytest; multi-turn arrival/halt cases |
| **W3** World generator | §6, fairness gates, PNG preview | user reviews sample worlds |
| **W4** Bots + sim | §8–9 | §9 acceptance gates |
| **W5** Persistence + clock | schema, scheduled resolve, dev manual clock | live world ticks on schedule |
| **W6** Frontend world view | §11 | art ratified; user can plan a world turn |
| **W7** Live playtest | the user plays a week | the user's verdict |

W0–W4 are headless and can run largely in parallel with W5–W6 once W0 is done.

## 13. Open questions

1. Is packing taking pulses 1–4 (vs a whole turn) the right cost?
2. Column speed: 12/6 hexes per turn after packing, 16/8 at full speed. That means
   ~7–14 turns (4–7 days) to cross a 110-hex world. Too slow? Rail can come later.
3. Should a stack of columns have a cap (e.g. a road hex holds 6) so mass
   stacking has a cost even before contact?
4. Starting world: 12 slots right for the first live world?
