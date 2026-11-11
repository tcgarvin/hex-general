# Vision: from skirmish to persistent war

Where hex-general is headed after v0.1. This is direction, not rules — `SPEC.md`
stays the source of truth for anything implemented, and [`WORLD_SPEC.md`](./WORLD_SPEC.md)
specs the first slice. Items marked **(decided)** were ratified by the user;
everything else is a working proposal. Numbers come from the 2026-11-08 sizing study
(§12).

## 1. The target

Scope and scale in the vein of NetEase's persistent-war SLGs (*LotR: Rise to War*,
*Infinite Lagrange*): a large shared map, many commanders, a season arc that
escalates from local land-grabs to multi-player assaults on great cities.

- **Scale (decided, 2026-11-08):** size the world for **200 player slots, expecting
  ~100 live at any time.** The slack is deliberate: beaten players relocate, new
  players arrive, and both need room.
- What we keep from the NetEase games: a map full of things worth taking, reasons to
  leave troops at home, cheap long-distance travel, and a season that unlocks bigger
  objectives over time.
- What we reject:
  - **Unconvincing movement.** Armies marching in straight lines over anything. Ours
    path over real terrain, roads, rivers and bridges.
  - **Army-building as the game.** No hero leveling, no unit-training grind, no "did
    you bring the right roster." Units stay roughly uniform (special units someday);
    **tactical interplay is the game.**

## 2. Pillars

1. **Tactics first.** Simultaneous planned execution, ZOC, terrain and combined
   arms stay the heart. A v0.1 match is, roughly, *one engagement* in the big game.
2. **A map with reasons.** Capturable sites with distinct strategic consequences,
   not just "go find the enemy army."
3. **Fronts are interesting; the rear isn't a chore.** Congestion, collision and
   one-unit-per-hex matter where armies meet. Marching, garrisoning and transit
   should be cheap and jam-free.
4. **Two logins a day is enough.** Still no reward for constant logins,
   micromanagement, or spreadsheet accounting (SPEC §18).

## 3. Turn clock (decided)

- **Two turns a day on a fixed clock:** one overnight (plan before bed) and one at
  **~1 p.m. Eastern** (plan around lunch). Other time zones are a later problem.
- Turns resolve on schedule, not when everyone locks. A unit with no fresh order
  keeps its standing behavior (hold / continue its multi-turn move).
- Consequence: an engagement "over some days" is ~6–10 turns, so each turn carries
  more weight than a v0.1 turn, and long-distance movement per turn is much larger
  (§5).

## 4. Automation (decided: not yet, for humans)

- **No doctrine or conditional scripting for human players to start.** Revisit later.
- **Multi-turn travel orders are in.** "Go there" may take several days to arrive.
- **Bots are automated by definition** (§9).

## 5. Transit: columns and auto-deploy

Two postures: **deployed** (v0.1 rules, one per hex) and **column** (packed for
travel: fast, stacks, can't attack, fights badly). Study results (§12):

- **Deploy distance X = 3 (proposal):** a column halts and deploys when its next step
  would come within 3 hexes of a hostile brigade (true positions, so columns can
  blunder into ambushes). Each extra hex of X cost ~1.5 points of front share; X = 3
  buys a hex of buffer outside enemy ZOC.
- **Where columns may go: open.** "Roads only" (columns exist only on roads and in
  towns) cost ~5 points of front share, mostly from walking deployed between road and
  line and from road networks that don't reach every front. Untested middle option:
  columns may leave roads at half speed but **stack only on roads and in towns**. If
  roads-only is chosen, the generator must guarantee roads reach every border and stay
  connected within each territory.
- **Stack cap 6 per hex (proposal):** stacks never passed 8 in simulation; a cap of 6
  caused almost no jams and keeps big stacks from being free.
- **Relief in place + friendly swaps (proposal):** an arriving brigade can swap hexes
  with the tired brigade it relieves, and friendly brigades may swap head-on. Without
  this, rotating a front line jams and slot clusters can lock for weeks. (v0.1 treats
  friendly swaps as a traffic jam, SPEC §8.2 — this is a rule change.)
- **Strategic movement (rail or similar) is a must-have, not a nice-to-have.** At world
  scale the median capital-to-capital march is ~8–9 days and alliance call-ups reach
  two weeks (§8).

## 6. Map, sites and economy

- **Objective density is the fix for sparseness.** Twin Rivers has ~1 objective
  per 200 hexes.
- **Ownership lives on sites, not every hex.** Provinces take their control and
  borders from an anchor site.
- **Resources: not decided.** Food, minerals, oil and factories were examples; the
  NetEase approach is a good model. Test for any resource: *losing it must change what
  you can do next week.*
- **Economy framing (decided, 2026-11-09):**
  - **Setting:** WWII-ish industrial: manpower, fuel (oil), industry (factories,
    mines), maybe food (farms).
  - **Base:** your home site plus a few buildings (barracks, depot, workshop and the
    like), built with resources. There is no single base level. The base stands on
    open ground beside a town, not on a city, with four hexes kept clear for it to
    grow over later; for now every base is one hex (user, 2026-11-10).
  - **Neutral sites are defended:** each has a small computer-controlled garrison,
    tougher farther from the capitals and on more valuable sites. Every capture is a
    small tactical fight, and the garrisons later become the marauders (§7).
  - **Slow growth:** a full-strength army (~15 units) takes **3+ weeks** (40+ turns),
    so most early fights are small.
  - The detailed design (site types and counts, building costs, growth curves) is
    drafted for review: https://claude.ai/artifact/BjDgptUsyxZn7aLBW5pxd4 (revision 2).
- **Economy decisions (user, 2026-11-09):**
  - **Resources:** manpower, fuel, steel. **Sites:** farms, oil fields, steel mills,
    factories, cities. **Buildings:** barracks, depot, workshop. Names approved.
  - **Army size comes from barracks and sites** (draft: `4 + 2 × barracks level +
    1 per 4 sites`, max 15). Tune later.
  - **Shortages disband units.** Out of fuel, the player picks a tank or gun to
    disband; out of manpower, any unit. (Draft: one unit per short turn, with the
    weakest as the default pick.) A manpower shortage does not stop healing.
  - **Bases are not cities.** Each player has a base, its own kind of site. Empty
    slots are vacant base sites that can't be captured.
- **Site decisions (user, 2026-11-10):** sites leave the towns. Farms, oil fields,
  steel mills and factories each stand on their own hex beside a town or a road, at a
  level 1–3 fixed at generation (higher levels rarer, richer). Towns stay as terrain
  (defence, repair) but own nothing; cities stay capturable sites. Every hex a base
  covers is fortified ground. Detail and provisional tuning: WORLD_SPEC §6–7. **"The Capital"** is saved for
    later as a season end-game objective rather than something every player has.
- **Sites unlock unit types (decided direction).** E.g. a **tank factory** enables
  armor, and the map must supply enough that **most players who work for one can get
  one.**
- **World size (proposal): ~350 × 350 (~122k hexes) for 200 slots** — ~610 hexes per
  slot when full (83% of battle groups keep an open flank), ~1,200 per live player at
  100 live (92%). Flank room levels off past ~600–900 hexes per player; bigger only
  lengthens marches.

## 7. Army size, rotation, rear pressure

- **Command cap grows with maturity:** a mature player fields ~12–15 brigades.
- **Expect about a third of an army in the line, not half.** With brigades worn down
  at ~1.5 HP/turn, rotating out at 4 HP and healing in towns, simulated front share
  was 32–41% depending on rules. Levers: relief swaps (+2 pts), 3 HP/turn healing
  (+1.4), sending replacements early (+4, a player habit). To field 7–8 brigades in the
  line at once, the cap would need to be ~22.
- **Host-nation rest (proposal):** brigades heal in any allied town, not only their
  owner's. For a big alliance's distant members this is the difference between ~46%
  and ~22% of brigades in the line.
- **Unrest / marauders:** uncovered sites accumulate unrest and spawn bot raiders —
  justifies garrisons, gives early PvE, soft-caps sprawl.
- **Relocation (decided direction):** a beaten player whose home is destroyed packs
  everything into columns plus a headquarters train, marches to unclaimed land and
  founds a new capital (minimum distance from others) — or gives up. Land of departed
  or beaten players reverts to neutral (marauders may hold it); that's the room people
  restart in.
- **New players** spawn near the active population (not at random into empty land) and
  get a protection window.

## 8. Players, factions, season arc

- Everyone starts as a **warlord.** Some stay one; most group into **alliances**
  (members stay separate players; allies don't fight and may march through each other's
  land), with chat and shared vision.
- **Alliance cap (proposal): starts at 5, grows to ~20 as the alliance matures.** In
  simulation, the farthest member's march to a single big push was ~6 days at 5
  members, 9 at 10, 11 at 20, 13 at 35, 15 at 50. Bigger than 20 needs faster strategic
  movement. Alliances that only grow by recruiting independents stopped at 8–14 on their
  own; a cap only bites if alliances can merge.
- **Operations need a commitment horizon (proposal).** Alliances that re-picked targets
  every ~10 days redirected 60–80% of big-push marches before they arrived. Alliance
  operations (and the bots that run them) should hold a target for ~2 weeks.
- **NPC governments** hold great cities too strong for one warlord.
- Season arc: warlord land-grab → alliances form → NPC cities fall → late-season
  objectives.

## 9. Bots are the product (decided)

First seasons will be mostly bots. Architecture (decided direction, 2026-11-08):

- **Two layers.** An **LLM strategist** makes high-level decisions; **hand-written
  tactical AI** (the existing planner, `AI_SPEC.md`) produces the actual orders.
- **The strategist chooses which tactical AI runs the turn, with which arguments**
  (e.g. "hold this front, 30% in reserve, raid that junction"), and takes structured
  diplomacy actions (propose / accept alliance, request help, declare war).
- **Chat:** the strategist reads chat and may leave **at most one message per turn**
  across a few channels/DMs.
- **Timing:** strategist calls arrive at random times across the 12-hour planning window,
  like humans. Safe because the board doesn't change during planning; the tactical AI
  runs as soon as the plan lands.
- **Cheap model** (e.g. DeepSeek) behind a model-agnostic client (any OpenAI-compatible
  endpoint). Rough budget: ~8k tokens in / 1k out per call, 2 calls/day/bot — on the order
  of tens of dollars a month for 100 bots at current cheap-model prices (verify).
- **Design rules (proposal):**
  - The LLM is never on the critical path: a failed or late call keeps the previous plan.
  - Input is a compact, **fog-honest strategic digest** (regions, fronts, relative
    strength, alliances, unread chat), not the board.
  - A small **persistent notebook** the bot rewrites each turn, so it remembers grudges
    and promises.
  - Game state never comes from parsing prose; chat is flavor, actions are structured.
  - Player chat is untrusted input (prompt injection); the fixed action schema caps the
    damage at legal moves. Outgoing bot chat gets a cheap moderation pass.
  - Open product question: label bots as bots in chat (recommended).
- **Testing:** the world simulator runs bots with a scripted stand-in strategist (fast,
  deterministic) and records real LLM decisions so a season can be replayed exactly.

## 10. Strategic UI (proposal)

The hex board stays the tactical view. Above it, a zoomed-out world view in the
spirit of the sizing-study viewers (§12):

1. **Theater** (full-screen): alliance-colored territory, front lines, army counters
   with strength, march arrows with ETAs, battle markers that open the tactical replay.
2. **Operational:** one region; brigade counters, columns, stacks.
3. **Tactical:** the existing hex board.

- **Replays:** a season timeline (like the alliance viewer) plus a per-turn "what
  happened near me" digest.
- **Fog-aware for players:** the theater view shows what your alliance knows, with stale
  intel marked ("last seen 2 days ago"). The omniscient view stays an observer/debug tool
  for bot worlds.
- **Art register (decided 2026-11-11, implementation not started):** the light
  **"staff-map"** style, a printed staff map with grease-pencil territory, front lines,
  pins and counters, drawn procedurally from the map data: the Field HQ map table taken
  to world scale. **"staff-map-night"** (the same linework on dark ops-room paper) is
  kept as a possible theme, for example for the full-screen theater view. At the switch
  to the painted tiles, the **grease layer (territory edges and fronts) stays on over the
  tiles** and the two layers **cross-fade over a short zoom band**, so what the player
  tracks does not change style. Picked from the round 1 mocks
  (`scripts/staff-map-mock.py`, notes in `.dev/staff-map/notes-r1.json`); the
  runner-up was "hybrid", and "painted-far" and today's overview were not recommended.
  Known gaps to solve when it is built: hashed owner hues collide among 100 players (a
  neighbour-aware or alliance palette); towns read as small rings at near zoom; the
  crayon hatching is in screen space and would swim during a zoom (move it to world
  space); fog and stale intel are not shown (the mocks are omniscient); and a
  whole-world texture at 10 to 22 px per hex is 3,500 to 7,700 px wide, so it needs
  chunked textures baked on demand or a WebGL pass over per-hex data textures. Tracked
  in `docs/TODO.md`.

## 11. Art generation (decided, 2026-11-08)

**New art generation goes to Codex** (the `codex` CLI and its built-in image
generation), not the NBP tile-foundry / `tile-smith` loop. The NBP foundry stays as the
record and reproducer for the tiles already shipped. Unchanged: the user ratifies every
art change from a hosted review page before anything is applied (CLAUDE.md).

## 12. Evidence: the 2026-11-08 sizing study

Standalone simulations (not in this repo) with an interactive write-up and replays:
https://claude.ai/artifact/AMdx9xH3SfcewwkM7m7gfB (private to the user).

- **Round 1, map size:** 100 players × 15 brigades, half engaged. ~280² (~800 hexes per
  player) is where flank room levels off; fronts close up below 200².
- **Round 2, columns:** roads-only −5 pts front share; X = 3 costs ~1.5 pts per hex over
  X = 2; relief swaps +2 pts and cut line jams by two-thirds; best combination 41%.
- **Round 3, alliances:** march times by alliance size (§8); host-nation rest; the
  two-week commitment horizon.
- **Follow-up, 200 slots:** ~350² keeps full-world fronts as good as 280² was for 100.

Simplifications throughout: no deaths, no conquest, fixed fronts. Treat the numbers as
sizing, not balance.

## 13. Sequence (slices)

**No 2-player detour (decided, 2026-11-08):** build and test against the world
itself, not the Twin Rivers 2-player game.

**World games only (decided, 2026-11-09):** after the switch, the server runs only
big world games. 2-player matches are retired, not kept alongside, so the world flow
replaces lobby, invite and vs-bot rather than adding a second mode. World work stays
local until that switch.

**Retirement done in code (2026-11-10, not yet deployed):** the 2-player match, its
`/games` API, lobby, invite link, vs-bot bots and the 2-player board UI are deleted from
`api/` and `frontend/`, and a migration (`20261110200000_retire_matches.sql`) removes
non-world games and drops `games.invite_token`. The deployed site still runs the old
build until the cutover deploy, and the turn-clock infra
(`infra/setup-world-clock.sh`) is still not applied.

**Cutover runbook (audited 2026-11-11).** In order:

1. Decide the world size and who may join. Agreed: a rehearsal world of about 60
   slots with about 30 bots on the `twice_daily` clock.
2. Dump the old games: declined. Losing the 6 old 2-player games is accepted.
3. Push `main`. The API, frontend and Supabase workflows run in parallel (plus the
   tests-only `scripts.yml`), so expect a few minutes where the new SPA and the old
   API disagree. The API deploys with `--memory 1Gi` (owner-approved, 2026-11-11).
4. Confirm the remote has all 14 migrations and a `game_maps` table.
5. Run `infra/setup-world-clock.sh` (owner confirmation first: it creates the
   `hex-clock` service account and the `hex-world-clock` Scheduler job).
6. `gcloud scheduler jobs run hex-world-clock` and check the job got a 200, not a
   403. The API requires `email_verified` in the OIDC token, which is unverified for
   Scheduler's tokens until this run. Then `verify-stack` (it checks the world routes,
   the clock config and the live commit).
7. Create the world with `scripts/prod-world.py create --slots 60 --bots 30`.
8. Join, submit a plan, and watch the first 01:00 or 13:00 America/New_York tick.
9. Add alerting on clock failures (the endpoint answers 500 when a world fails).

Facts the audit established:
- `retire_matches` deletes all 6 production games (auth users survive) and drops
  `invite_token`, so the old API revision cannot be rolled back to after it runs.
- Cloud Run ran at 512 MiB / 1 CPU / 300 s timeout / scale to zero, and `api.yml`
  set none of these; it now sets only the memory (1 GiB). One 200-slot world measured
  387-410 MiB across ticks in the production image, and `MapCache` holds 4 maps, so
  more than one big world per instance would not have fit in 512 MiB.
- Clients poll every 10 s (every 2 s once a turn is due).
- A failed tick leaves the turn due: plan submission answers 409 until a tick
  succeeds. `turns.state_after` is the natural restore point.

**Before the live playtest (decided, 2026-11-09):**
- **Fun before contact.** The pre-contact loop of expansion and economy has to be fun
  on its own.
- **Economy design first.** Settle the basic economy before the playtest: what sites
  there are, how many you need to grow your power and your base, and what each changes
  in play (§6).
- **Bots as they are.** Improving the bots waits until the user's first round of
  testing. The current bots are there to make the map feel full and active.

1. **Open world** — [`WORLD_SPEC.md`](./WORLD_SPEC.md): N-slot world, generated map,
   two-a-day clock, columns + auto-deploy, multi-turn travel, relief swaps, growing
   command cap, world bots + headless world sim, frontend world view.
2. **Sites + unit unlocks**, and **strategic movement** (rail or similar).
3. **Unrest / marauders**, relocation, new-player placement.
4. **Alliances, diplomacy, chat, LLM strategists, NPC governments, season arc.**
5. **Scale-out** when measurements demand it: chunked state, per-turn resolution
   split into independent contact clusters.

## 14. Open questions

- Columns: anywhere vs roads-only vs "stack only on roads" (§5).
- The resource set (§6).
- Whether, and when, to give humans standing orders or doctrine (§4).
- How to handle other time zones.
- Bot disclosure in chat (§9).
