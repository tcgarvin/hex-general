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
  bridges where roads cross them; lakes (impassable open water, never bridged). Every
  river and lake drains off the map (user, 2026-11-10): rivers rise at springs by
  hills or at lakes and always run to the map edge.
- **Settlements:** about 1 per 70 hexes [tunable], roughly 3× v0.1's density, mostly
  towns. Cities are rarer and anchor regions.
- **Roads:** a spanning network linking settlements plus redundant links, so no
  single bridge decides a whole region. Every town in a territory must reach its seat by
  road inside the territory, and roads must reach its borders. (Until 2026-11-10 every
  road scrap in a territory had to join the seat's network; that laid doubled roads along
  borders, so through-roads that only clip a territory are now left alone.) In the study,
  52–60% of territories had split road networks, which stranded brigades under roads-only
  movement.
- **Provinces:** every site anchors a province (about 20–40 hexes), assigned
  by travel-cost distance. Province control is derived from its anchor site,
  with no extra state. Provinces exist for borders and the strategic view.
- **Start regions:** capitals spaced by Poisson-disc sampling. Each player starts
  with a base (not a city) and 4 units (2 infantry, 1 armor, 1 artillery) [tunable].
- **Fairness gates** (generator tests): every settlement is road-reachable; every
  start has a similar settlement count and road access within radius R; no start
  is within X hexes of another.
- **Review:** the generator emits a PNG preview (terrain, roads, provinces, starts)
  for map-design review. Hex tile art is unchanged; this is a debug render, not board art.

**As built (W3):** `app/world/generate.py` with the terrain stage in `app/world/land.py`
(pure Python, no numpy; a 200-slot 349² world takes ~10 s), `app/world/preview.py`
(stdlib PNG, `--style territory|natural`, `--no-roads`), gates in `tests/world/`.

**Terrain, rivers, settlements and roads (redesigned 2026-11-10, awaiting the user's
review).** The old layout was thresholded noise: woods and hills in single patches of
300–650 hexes, ~40% of land with only plains within 3 hexes, rivers that walked in from
the edge and stopped in open fields, evenly spread towns, and parallel double roads
from the fix-ups. Terrain is now built from sized objects, in this order:

- *Highlands:* hill country is the band around the mid contour of a broad noise field,
  so rugged ground forms belts across the map; each hex also gets the hex axis along
  the belt.
- *Hills* (`hills_share` 7%): ridges walked along that axis (80% of the time) with ±1
  wobble, 1–18 hexes long, sometimes two wide, with a pass now and then. Seeds are drawn
  in proportion to ruggedness⁴. Separate ridges never touch, so no hill patch exceeds
  30 hexes.
- *Lakes* (`Terrain.LAKE`, impassable, never bridged or roaded): about one per
  `hexes_per_lake` (1,000) hexes, 3–22 hexes each, placed by best-candidate sampling
  on low ground, grown raggedly, at least 3 hexes from the map edge and more than 5
  from each other, never on hills, holes filled.
- *Rivers* (`river_share` 2.4% of the map, bridges included): **every lake and river
  drains off the map.** Rivers rise at lakes or at springs at the foot of hills and
  always run to the map edge. Lakes are drained first, nearest to drained water first,
  each by one outflow into the edge, a lake that already drains, or a clean Y junction
  on a river that already drains; a lake that cannot be drained is removed (2 of 40
  12-slot seeds lose one). Springs then add tributaries into drained water or the edge
  until the share is met; a spring is at least 3 hexes from the edge, so a river end on
  the edge always means it flows off the map, and the hills beside a spring are kept
  (no town, site or base plot on them). A course is a least-cost path that turns at
  most 60° a step, never twice the same way running, avoids hills, touches no other
  water but where it ends, and never walls off land under 40 hexes. Rivers stay one
  hex wide with no sharp turns and no triangle of river hexes.
- *Woods* (`woods_share` 14.5%): copses to forests of 2–30 hexes, grown as ragged
  blobs, seeded by a steep regional field (some country open, some close) plus bonuses
  beside hills and along some banks; two open hexes between separate woods; hexes 3
  deep in a forest become glades. No woods patch exceeds 30 hexes.
- *Settlements:* best-candidate sampling weighted by appeal: plains over woods over
  hills, a waterside bonus, fewer in rough ground and highlands. *Cities* (15%) are
  picked by farthest-point sampling over the settlements, leaning to open and waterside
  ground, so they are spread-out hubs (seats chosen on a city become towns).
- *Roads:* step costs by terrain (plains 1, woods 2.2, hills 3.2, bridge 7), cheap
  reuse of road (0.4), a smooth noise wiggle so roads are not ruler lines, a penalty
  for running beside an existing road, never on a lake. A bridge goes only on a river
  hex with exactly two river neighbours and no lake beside it (not at a junction, a
  river end or a lake mouth). Sharp turns over new ground are removed from each laid
  road; the only sharp road corners left are at towns beside a through-road.
- *Natural road style (the default for new worlds, user, 2026-11-11):*
  `WorldParams.road_style` is `"natural"` (`app/world/roads.py`, the default,
  `DEFAULT_ROAD_STYLE`; seeds pinned by a digest test) or `"direct"` (the roads above,
  kept as an option: every seed regenerates byte-identical to the worlds made before
  the natural style existed, pinned by a digest test). Worlds already created keep
  their roads: a world's map is stored once in `game_maps` and always read back from
  there, never regenerated from its seed (§10.1). The CLIs (`app.world.preview`,
  `road_metrics`, `sim`, `season`) take `--road-style direct` for the old style.
  Why direct roads read as a lattice: a hex step costs the same at any heading, but a
  heading 30° off the axes is a zig-zag that gains only cos 30° per step, so least-cost
  search prices off-axis travel ~15% dearer and picks straight axis legs joined by 60°
  corners (21% of road hexes on straight runs over 8, longest 24–30). Natural style:
  the search carries its heading and charges a 60°-turn hex cos 30° of its price (the
  drawn road cuts the corner), so any heading costs the same per distance; never a 120°
  turn on new ground; each free stretch is then smoothed and re-laid on hexes when that
  stays valid and no more than 6% dearer. Costs: plains 1, woods 3.4 (woods have no
  road art, so a road there vanishes), hills 3.6, a slow noise (±40%, ~16-hex
  wavelength), +50% at full ruggedness, ×0.85 beside a river, +1.6 beside another
  road. Bridges only on river shapes with bridge art (straight N-S, and the two V
  bends), entered and left by the tile's baked ramps; elsewhere only as a last resort
  (cost 30, crossed straight), and a road that still finds no way is laid as in the
  direct style (counted in `stats["natural_road_fallbacks"]`, 2–4 per 200-slot world).
  Land, towns and seats are unchanged (the noise has its own seeded generator).
  Measured on seeds 1–3, 200 slots (direct → natural): straight runs over 8 hold
  19–21% → 4.5–5.4% of road hexes, longest run 24–30 → 15–16, 60° turns 28–30% →
  61% of two-way road hexes; bridges 190–204 → 165–174, drawn along their ramps 12% →
  95%+; road hexes in woods 508–537 → 317–340; road distance between neighbouring
  towns over straight-line distance, median 1.11–1.13 → 1.20 (p90 2.4–2.5 → 2.8–2.9);
  column pulses base to neighbouring base, median 37–38 → 38–39 (worst 65–66 → 62–71),
  base to own sites median 15 → 16. Generation 27 s → 41 s. Review tool:
  `scripts/road-study.py` (metrics, frontend-rule board check, board-mock exports).
- Seed 1: 12 slots: woods 14.5%, hills 7.0%, river 2.3%, lake 0.7% (7 lakes, 14
  rivers), 10 bridges, road 14.0%; largest woods patch 30, hills 19. 200 slots: woods
  14.4%, hills 7.0%, river 2.2% plus 194 bridges, lake 0.9% (122 lakes, none dropped,
  155 rivers), road 14.7%; largest woods and hills patches 30.

- *Territories* (start regions) are each capital's nearest land in steps over passable
  hexes, so roads never move a border. Fix-ups lay roads only inside territories: every
  settlement reaches its seat by road inside its territory, a road crosses every border
  with ≥ 4 hex contacts, and new towns go to any territory below half the median
  settlement count. A road fragment with no settlement (a through-road clipping a
  territory's corner) is left unjoined (2026-11-10): joining those laid parallel double
  roads along borders.
- Seats: farthest-point sampling, 6 Lloyd steps on open hexes, then each seats on a
  nearby settlement (cities preferred) at least 60% of the ideal lattice spacing from
  the others. The seat becomes a town; territories are each seat's nearest land.
  Provinces average ~70 hexes at the default density, not 20–40.
- `fill_order` orders slots so any prefix is spread out; `to_state(players)` seats the
  first N, each with a garrisoned base and the other three units beside it.

**Sites off the towns, site levels, base plots (user decisions, 2026-11-10):**

1. **Sites leave the towns.** Farms, oil fields, steel mills and factories each stand
   on their own hex beside a town or a road. Towns stay as terrain (defence and repair
   as before) but hold no ownership: no `Settlement`, no site. Site counts and the
   economy are unchanged (one site per former town site, dealt per territory by the
   same quotas). City sites are unchanged: on their city hex, capturable.
2. **Bases are not on cities.** A base (and each vacant base slot) stands on open
   ground with a four-hex footprint kept clear: axial offsets `(0,0)`, `(1,0)`, `(0,1)`,
   `(1,1)` from the anchor (`BASE_FOOTPRINT`). **Every base is size one for now**: only
   the anchor is the base; growth is deferred.
3. **Site levels.** Farms, oil fields, steel mills and factories have a level 1–3
   (`MapSite.level`), fixed at generation, drawn at random with higher levels rarer.
   Cities and bases are level 1.
4. **Fortified ground.** Every hex a base covers (today just the anchor) gives the
   settlement defence and repair bonus. Capturing a base means taking its anchor.

As built (provisional tuning, all of it): site and base hexes are ordinary plains (no
new terrain); `GameState.settlement_ground` is the one predicate for "+2 defence and
settlement repair here": town or city terrain, or a site hex whose settlement exists
(a vacant base slot gives nothing). Levels are drawn 60/30/10% (`SITE_LEVEL_WEIGHTS`).
A seat is a town and gets no site; its base anchor is the nearest spot within 3 hexes
(5 if needed) whose four footprint hexes are on the map, off the edge ring, in the
slot's territory, not river, bridge, lake, town, city, road, a spring's hills or
another plot, with one hex
touching a road. A town's site goes within 2 hexes (3 if needed) on such a clear hex
in the town's territory, beside the town or a road (a town wedged into a corner of its
territory with no such hex may put its site in a neighbouring territory within 3 hexes,
counted in `sites_off_territory`, 2026-11-10); preferred: plains, beside a road,
beside the town, not beside another site, nearest. Woods or hills under a plot or site
become plains; no fit raises `WorldGenError`. Units never start on a site or plot hex.
Seed 1 (terrain redesign, 2026-11-10): 12 slots convert 1 site and 9 footprint hexes,
none widened; 200 slots convert 8 and 157, none widened.
- Known spread (seed 1, 200 slots, terrain redesign): territory size 240–878 hexes
  (middle half 504–677); settlements other than seats within 14 hexes of a capital 3–15
  (median 9). Over seeds 1–40 at 12 slots territories run 260–1,035 hexes with 4–21
  settlements; seed 2 at 200 slots has one territory of 116 hexes. Edge and river starts
  run small. The W4 sim will show whether it matters.

## 7. Economy and army size

The reviewed economy draft (https://claude.ai/artifact/BjDgptUsyxZn7aLBW5pxd4,
revision 2; decisions in VISION §6) replaces v0.1 muster in world games.

- **Resources:** manpower, fuel, steel, each with a storage limit (30, then 50 and
  80 with depot levels).
- **Sites** (`MapSite`, stored with the map): base, city, farm, oil field, steel mill,
  factory. A city site is its city hex; the others stand on their own plains hex
  (§6, 2026-11-10). Per territory: one factory 11–20 hexes from the base, one oil
  field and one steel mill within 12, one more of either farther out where there are
  6+ towns; the other towns' sites are farms. Bases are their own site kind (not
  cities); an unfilled slot's base site is vacant (no settlement, can't be captured)
  until a player founds a base there.
- **Site levels:** farms, oil fields, steel mills and factories are level 1–3. Each
  level above 1 adds +1 of what the site makes (`WorldEconomy.level_income`): farm
  manpower 2/3/4, oil fuel 3/4/5, steel mill steel 2/3/4, factory steel 1/2/3
  [provisional tuning, 2026-11-10].
- **Garrisons:** every neutral site starts with a neutral garrison (owner `neutral`,
  hostile to all, always holds, never captures). Tier by distance to the nearest base
  and value: T1 militia (infantry at 5 HP), T2 infantry, T3 infantry + gun, T4 two
  infantry + gun; a level-3 site is one tier tougher, up to T4 [provisional]. No
  respawn and no regeneration yet.
- **Buildings** at the base, one under construction at a time: barracks (5 levels),
  depot (2), workshop (1, unlocks guns). Tanks recruit only at a held factory;
  infantry and guns at the base or a held city.
- **Army size:** `4 + 2 × barracks level + 1 per 4 held sites` (the base doesn't
  count), up to 15.
- **Shortages:** each turn the stock can't cover upkeep, one unit disbands per short
  resource (fuel: a tank or gun; manpower: any unit). The player's `disband` pick goes
  first, else the weakest (lowest HP, then newest).
- **Healing:** resting on settlement ground (a town, a city, or a founded site's hex)
  restores **3 HP/turn**; the same ground gives +2 defence.
- **Elimination:** a player with no settlements and no units is out of the world.
- No victory condition in slice 1; the world simply runs.
- *As built (2026-11-10):* rules data `app/engine/economy_rules.py` (`WorldEconomy`,
  carried in `GameConfig.economy`, so each world can be tuned), the phase
  `app/engine/world_economy.py`, world recruitment in `recruitment.py`, validation in
  `app/games/validation.py`. Submissions carry `build` and `disband`
  (`EconomyOrders`). Other players' stocks and base are hidden by fog. The v0.1 muster
  path remains in the engine, selected when `GameConfig.economy` is unset (engine
  tests use it); the 2-player flow that used it was retired on 2026-11-10, so
  removing it is now an open cleanup.

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

**Perf pass (2026-11-11).** Measured on the 200-slot map with 100 bots, seed 1:
turns 35 to 59 saved and resolved again (about 2,900 units and 1,300 orders a
turn), on a shared 16-core machine under heavy load, best of five with cold
geometry caches. Resolution went from 0.76 s median and 1.0 s worst to 0.57 s
median and 0.69 s worst. Redacting the views of all 100 players went from 4.6 s
to 1.4 s (one player: 0.04 s to 0.01 s). The whole 60-turn season, bots
included, went from 596 s to 309 s. The fixes were indexes and shared values,
with byte-identical turns: one shared coordinate object per hex in the geometry
caches, a per-phase snapshot of settlement ground, a cached set of
sight-blocking hexes, and fewer unit copies. The slow outliers left are Python's
full garbage collections, which walk the whole map (one world state is about
650,000 tracked objects): one costs 0.15 s with a single state alive and up to
0.9 s with several, whenever it lands inside a resolve. Freezing
the loaded map out of the collector (`gc.freeze()` after load) is the next step
for the server and the simulator. After that, the remaining profile is flat
(column contact checks, unit copies, route search), and the next real gain
needs a design change: resolving independent contact clusters separately (§10).

**Collector pauses (2026-11-11).** Every world turn now runs inside
`app.world.collector.quiet_collector`: the server tick, the simulator, the
season recorder and the bench. It turns Python's cyclic garbage collector off
for the turn and collects only the young generations when the turn ends. Over
a 100-bot season the collector never found garbage in the engine's objects
(they form no reference cycles), so its pauses were pure cost. Measured with
the collector's own time taken from `gc.callbacks`, on the same loaded machine
with the runs side by side. In a 60-turn, 100-bot season (turns 21 to 60), the
collector's time per turn fell from 0.61 s median and 1.2 s worst to 16 ms,
resolution from 0.81 s median and 1.38 s worst to 0.54 s and 0.80 s, and the
worst bot-turn from 0.59 s to 0.11 s. Replaying ten saved turns (30 to 57)
the way the server ticks (state loaded, fresh bots, resolve), with the
variants interleaved, the collector's time per tick fell from 0.55 s to 0.13 s,
all of it in the pass at the end. Peak memory (about 300 MB) and the season record are
unchanged. The alternatives lost: freezing the loaded state (`gc.freeze()`)
only shortened the full pass and left about 0.2 s of young collections inside
each tick; turning the collector off and running a full collection at the end
cost 0.37 s per tick; raising the full-collection threshold also left the young
collections and changes the whole process.

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
- **`games`** gains `clock` (`manual` | `twice_daily`) and `next_tick_at`. The
  `kind` column drafted here (`match` | `world`) was never built: the 2-player match
  was retired on 2026-11-10 (migration `20261110200000_retire_matches.sql` deletes
  non-world games, makes `games.map_id` NOT NULL and drops `games.invite_token`), so
  every game is a world and there is nothing to distinguish. `games.state` and
  `turns.state_after` hold the state *without* its map. The repo rejoins the cached map
  on load, so the engine still sees one `GameState`.
- **Slots ✅ (built 2026-11-09):** `game_maps.slots` holds the capitals in fill order
  (each slot's base anchor hex, on open ground beside its seat town, since 2026-11-10),
  and fill position k is player `s<k+1>` (`s001`, `s002`, …). `GameService.create_world`
  generates the world, stores the map, seats the bots (`bot_policy = "world"`) and
  starts the game active. `join_world` seats a human in the first open slot
  (`app.world.seating`): never held, base site still vacant, and no unit within 3 hexes
  of it. The seated state is written first, as a compare-and-set on `games.revision`,
  so two people joining at once get different slots and a join during a tick loses no
  write. The `players` row is inserted second; `(game_id, slot)` and
  `(game_id, user_id)` are unique. If that insert fails, the join takes the seat back
  out of the state (`seating.unseat_player`: the player, their units and their base
  go) and fails, so no slot is left seated with nobody behind it; a double-click join
  whose twin took another slot first answers 409, and an insert that committed before
  its error stands. One active world per user is checked in the app, not the schema,
  so the same user joining two *different* worlds at the same instant could still
  hold both (closing that needs a database-side check).
- **Tick (service ✅ 2026-11-09, routes and scheduler to come):** `tick_world` plans
  every bot from its fogged view (personalities seeded from the game id), adds the
  humans' stored submissions, and resolves. A world submission is stored but never
  resolves the turn itself. Measured on local Supabase with 200 slots, 100 bots and
  one human: ~1.1 s per tick, 1.8 s on a cold process. Every state write is a
  compare-and-set on `games.revision` (`StaleGame` on a lost race). Joins and ticks
  retry from a fresh read up to 3 times, and a tick writes the state before its
  `turns` row, so a lost race leaves nothing behind. World players may revise their
  orders until the tick (an upsert on `turn_orders`). Known gap: a submission that
  lands during the ~1 s tick is stored for the turn that just resolved and is
  ignored. *Closed 2026-11-10:* orders for a scheduled world are refused (409) once its
  turn is due, and every world submission re-reads the game after storing and is
  refused if a tick won the race, so orders are never silently dropped.
- **Scheduled clock ✅ (built 2026-11-10, not yet deployed):** a world is created with
  `clock` = `manual` or `twice_daily`. A scheduled world stores `next_tick_at`
  (`app/world/clock.py`: the next 01:00 or 13:00 America/New_York, DST-aware; on the
  autumn changeover the first 01:00). `POST /internal/clock` resolves every active
  scheduled world that is due, and moves its `next_tick_at` in the same
  compare-and-set as the state, so a repeated, overlapping or late call never resolves
  a turn twice (a late clock resolves one turn and reschedules from now). One failing
  world doesn't block the rest; the call then answers 500 so the scheduler retries.
  The caller is Cloud Scheduler with a Google OIDC token for `hex-clock@`, verified
  in-app (`app/games/scheduler_auth.py`: Google signature, issuer, audience, verified
  email), because the API is public. With `CLOCK_AUDIENCE` or `CLOCK_INVOKER_EMAIL`
  unset the endpoint answers 503. `POST /dev/clock` runs the same sweep for an admin, and
  `python -m app.world.tick_loop --game ID --every 300` is a fast clock for a local
  playtest. The `WorldView` carries `clock` and `next_tick_at` for a countdown.
- **Routes ✅ (built 2026-11-09, `app/games/world_routes.py`):**
  - `GET /worlds` lists worlds and `GET /worlds/active` returns the caller's seat
    (`{"active_world": {"game_id", "slot"} | null}`).
  - `POST /worlds/{id}/join` takes a slot.
  - `POST /worlds/{id}/orders` stores the caller's plan until the tick.
  - `GET /worlds/{id}` returns the fogged `WorldView`, whose `WorldState` is
    `GameState` minus the map.
  - `GET /worlds/{id}/map` returns the `CompactMap` with `Cache-Control: immutable`.
  - `GET /worlds/{id}/turns/{n}` returns the fogged replay.
  - Admin-only (built 2026-11-11, the admin screen at `/admin`): `GET /dev/worlds`
    lists every world whatever its status with its size, seats (humans, bots,
    open), turn, clock, next tick, created time and seed, plus the clock's zone and
    hours; `POST /dev/worlds` creates a world (bots may not exceed slots);
    `POST /dev/worlds/{id}/tick` is the manual clock; `DELETE /dev/worlds/{id}`
    removes a world (CORS allows DELETE for the browser); `POST /dev/clock` runs the
    sweep. The `/dev` prefix is historical. An admin is a dev (`DEV_USER_IDS`) or an
    account on `ADMIN_USER_IDS` (`require_admin` in `app/games/deps.py`, checked on
    the real account). The admin list grants no impersonation: `X-Dev-Profile` stays
    dev-only, and `GET /me` reports `is_dev` and `is_admin`. Production sets no
    `ADMIN_USER_IDS`: the owner is an admin as a dev, and the seeded `playtest*`
    accounts are not admins there because they are password logins and the
    production site offers Google sign-in only, so they could not reach the screen.
    Adding an account later is one entry in `api.yml`'s `--set-env-vars`.

  *Retired 2026-11-10 (in code, not yet deployed):* the server runs only world games.
  The 2-player `/games` routes, the match service paths, `app/games/routes.py`
  (shared dependencies now live in `app/games/deps.py`), the 2-player bots and their
  arena, and the invite token are all deleted, and orders now go to
  `POST /worlds/{id}/orders`. There is no `NotAWorld` guard because there is nothing
  else to be: every `games` row is a world.
- **Memory budget (measured 2026-11-10):** one 200-slot world fits the 512 MiB Cloud
  Run instance. In the production image under a hard 512 MB limit, creating the world
  left 286 MiB in use, and 25 ticks held between 387 and 410 MiB. That depends on
  `clear_geometry_caches()` running after every world tick: left alone, the hex
  geometry caches grow ~15 MB per tick. A decoded map is ~125 MB, so a second big world
  on the same instance needs more memory (or a `MapCache` of one).
- **Infra** (written, not applied; confirm at cutover: the retirement of the 2-player game is in code but not deployed): `infra/setup-world-clock.sh`
  creates the `hex-clock` service account and one Cloud Scheduler job for all worlds
  (`0 1,13 * * *`, America/New_York, OIDC, 5 retries). The API deploy already sets
  `CLOCK_AUDIENCE` and `CLOCK_INVOKER_EMAIL`.

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

**As built (2026-11-11): camera and keys.** The camera is clamped so the map always
fills most of the screen: on an axis where the map is smaller than the view it is
centred, otherwise at most 30% of the view may be empty past the map's edge. The clamp
applies after drags, the wheel, keys, minimap jumps and window resizes, so zooming all
the way out ends with the world centred. Jumps (centre on base, fit, minimap, a unit
picked from the Army panel) ease over 200 ms, or happen at once when the browser asks
for reduced motion. Picking a unit in the Army panel only moves the camera when the unit
is off screen or under a panel. Keys: W A S D or the arrows pan while held (Shift is
twice as fast), + and - or E and Q zoom, Home or Space centre on your base, F or 0 show
the whole world, Tab and Shift+Tab (or . and ,) step through your units with idle ones
first, T, M and B pick Travel, Move and Barrage mode, R and H order Rest and Hold, X or
Delete clear the unit's order, Esc leaves the mode and then deselects, Ctrl+Enter
submits the plan, and ? opens the field manual. Keys do nothing while typing in a field or
with Ctrl, Meta or Alt held (except Ctrl+Enter). The same camera actions are buttons
above the minimap.

**As built (2026-11-11): field manual.** `?` (key or button) and the account menu open
a field manual over the board (`frontend/src/world/FieldManual.tsx`): folder-tab pages
for the turn, your army, orders, columns and contact, sites and garrisons, your base
and economy, reading the turn, and the key list, with a search box; Esc closes it. Its
numbers come from the world's economy config and `state.config`, the planner's mirrors
of the engine (`UNIT_STATS`, `COLUMN_RULES`, `DEPLOY_DISTANCE`), and
`world/manualRules.ts` for the rest, which `manualRules.test.ts` checks against the
engine's Python source. Move reach follows the engine's occupancy rules: your own deployed
units block unless they are leaving this turn, a hostile hex can end a move (an attack)
but not be passed, and an engaged unit can't step from one hostile-adjacent hex to
another.

**As built (2026-11-11): relief swaps and the turn report.** A relief in place or
friendly swap (§4.7) is ordered from the board with Shift+click on a neighbouring unit
of yours while one of your deployed units is selected, or with the "Swap with" buttons
the unit panel shows when a deployed friend stands next door. It stages two one-hex
Move orders, each unit into the other's hex, so both step on pulse 1, where the engine
resolves the pair as a swap (`_friendly_cycles` in `movement.py`). The swap is refused
when both hexes touch a visible hostile deployed unit, because ZOC would block both
steps. The Army list reads "relieve (q,r)" for the unit stepping up into a hex next to
the enemy, "relieved by ..." for the one falling back, and "swap with ..." otherwise.
Clearing either order clears both. The turn report lists income and upkeep, then the
build and recruits paid after them, and ends on the stocks the header shows. A column
that halted on contact gets one line, and when no other player's unit is in sight near
it, the line names the garrisoned site it most likely met, from the static map. Each
fight is one line with both sides' losses and the HP left. Each report line keeps the
index of the replay event it came from (`eventIndex`), for playback to highlight it.

**As built (2026-11-11): turn playback.** "Play turn N" on the report header (and
"Watch turn N" on the turn-resolved banner) replays the last resolved turn on the board:
a barrage beat, one beat per movement pulse the viewer witnessed, then captures,
repairs, supply and recruits. Units slide between hexes, attackers lunge, fights flash
with damage numbers, shells arc from the gun, captured sites ring in the new owner's
colour, and columns halting on contact show a warning mark. The transport bar replaces
the plan bar: step back, play/pause, step forward, speed (0.5x to 4x), a scrubber with
your fights marked, the beat's name ("Pulse 3 of 8") and Exit. Keys: Space plays and
pauses, Left and Right step a beat, Esc exits; W A S D, Up/Down and the zoom keys still
move the camera, and moving it by hand pauses camera-follow until the next beat with an
action of yours (or Follow). The report line of the current beat is highlighted and a
click on a line jumps to its beat; "Your fights" at the top lists your engagements and
opens playback paused on one. Beats without any action of yours play at about half
length; with reduced motion the bar steps beat by beat without tweening. While it
plays, planning marks, hover previews and board clicks are off; Exit restores the live
board. The start picture is the previous turn's replay, fetched on Play (turn 0 rewinds
the end state instead); because the server fogs a replay with the end-of-turn sight,
enemies appear when an event names them or the viewer's sight reaches them and fade out
when it leaves them, and anything that moved wholly out of sight is not shown. Dev
builds only: `?turn=N` on the world URL shows and plays an earlier resolved turn, and
`window.__hexPlayback` drives the transport from the console. Code:
`frontend/src/world/playback/`, `PlaybackBar.tsx`, the playback mode in `WorldRenderer.ts`.
Playback draws every site's owner as of the played turn (its own end state, with captures
flipping at their beat), so an earlier turn does not show today's ownership.

**As built (2026-11-11): planning aids after the second playtest.** Your units go by
designations by type and seniority ("1st Infantry", "2nd Infantry", "1st Armor"), kept in
the browser per world and seat, so survivors keep their numbers when a unit dies and a
recruit takes the next one (`designations.ts`; `#id` stays in the unit panel). In Move mode,
hovering a hostile hex the unit can reach shows the expected damage both ways and the
defender's modifiers; in Barrage mode, hovering a hex in range shows the expected damage
to each unit seen there; the unit panel repeats it for a staged attack or barrage. The maths
(`combatPreview.ts`) is pinned to the engine by `__fixtures__/combat_fixture.py`, which runs the
real combat and barrage code over a table of matchups. In Travel mode, Shift+click adds a
waypoint to the selected unit's route (up to 4, drawn as diamonds, refused at the click when
the server would refuse it), Shift while hovering previews it, and the unit panel removes one
or all; Shift+click on a deployed unit of yours next door still means a swap. The unit panel
warns when a Move steps a unit away from an adjacent enemy, since a unit whose hex is attacked
on pulse 1 is pinned before it moves, and when a Travel is given to a unit that cannot pack. An
unsent draft records the server plan it was forked from; if a newer plan was submitted
elsewhere, the draft is set aside with a "Use my draft instead" choice rather than restored
over it. Recruits count a barracks or workshop that finishes this turn, as the engine does. A
turn replay withholds other players' private fields: the cost and stocks of a recruit, the
shortage and pick behind a disband, and a gun the viewer cannot see (`app/engine/fog.py`).

## 12. Implementation phases

| Phase | Delivers | Gate |
| --- | --- | --- |
| **W0** Engine generalization ✅ | indexed lookups, shared static map, `hostile()`, perf baseline (`app.world.bench`). The static/dynamic *storage* split and N-slot turn flow moved to W5, where persistence changes | v0.1 suite green; 350² / 1,500 units resolves in 0.05 s, one player's fog in 0.006 s |
| **W1** Columns ✅ | posture, March/Deploy orders, pack/deploy, column movement + stacking, auto-deploy + placement, disorder, barrage vs stacks, §4.7 friendly swaps and rotations | `tests/engine/test_columns.py` |
| **W2** Travel ✅ | `TravelOrder`, routing, standing orders in state, validation, `TravelEnded` | pytest; multi-turn arrival/halt cases |
| **W3** World generator ✅ (review pending) | §6, fairness gates, PNG preview | user reviews sample worlds |
| **W4** Bots + sim (first cut) | §8–9: `app/ai/world.py`, `app/world/sim.py`, AI_SPEC §12 | §9 acceptance gates (not yet met: see AI_SPEC §12.4) |
| **W5** Persistence + clock ✅ (economy and clock built 2026-11-10; 2-player flow retired in code 2026-11-10; scheduler infra and deploy still pending, at cutover) | schema (✅ `game_maps` + `games.map_id`, map stored once), scheduled resolve, dev manual clock | live world ticks on schedule |
| **W6** Frontend world view (built 2026-11-10: `/world/:id`, pooled tile renderer + overview/minimap, economy header, world list on the start screen; planning: travel with client-side route preview (TS port of `routing.py`, parity-tested against a Python fixture), move/barrage/rest/hold/deploy, build, recruit, disband pick, revisable submission returned in `WorldView.submission`; painted site icons; last-turn report (your fights, base, movement; witnessed events folded) with trails, fights and captures drawn on the board. Next: column art, pulse-by-pulse playback if wanted) | §11 | art ratified; user can plan a world turn |
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
