# Vision: from skirmish to persistent war

Where hex-general is headed after v0.1. This is direction, not rules — `SPEC.md`
stays the source of truth for anything implemented. Decisions marked **(decided)**
were ratified by the user on 2026-11-08; everything else is a working proposal.

## 1. The target

Scope and scale in the vein of NetEase's persistent-war SLGs (*LotR: Rise to War*,
*Infinite Lagrange*): a large shared map, many commanders, a season arc that
escalates from local land-grabs to multi-player assaults on great cities.

What we keep from them: a map full of things worth taking, reasons to leave
troops at home, cheap long-distance travel, and a season that unlocks bigger
objectives over time.

What we reject:

- **Unconvincing movement.** Armies marching in straight lines over anything.
  Ours path over real terrain, roads, rivers and bridges.
- **Army-building as the game.** No hero leveling, no unit-training grind,
  no "did you bring the right roster." Units stay roughly uniform (special units
  someday); **tactical interplay is the game.**

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

- **Two turns a day on a fixed clock:** one overnight (plan before bed) and one
  at **~1 p.m. Eastern** (plan around lunch). Other time zones are a later problem.
- Turns resolve on schedule, not when everyone locks. A unit with no fresh order
  keeps its standing behavior (hold / continue its multi-turn move).
- Consequence for tuning: an engagement "over some days" is ~6–10 turns, so each
  turn carries more weight than a v0.1 turn, and long-distance movement per turn
  must be much larger than today's 2–4 hexes (see §5).

## 4. Automation (decided: not yet)

- **No doctrine or conditional scripting for human players to start.** The user's
  original objection to it may still hold; revisit later.
- **Multi-turn travel orders are in.** "Go there" may take several days to arrive —
  the NetEase baseline, and necessary under a two-a-day clock.
- **Bots are automated by definition.** AI commanders plan their own turns; the
  LLM strategic layer (§9) assigns objectives/scripts to bot armies.

## 5. Transit: columns and auto-deploy (proposal)

The user's "pack them like troops on a train" idea, as two movement states:

| State | Movement | Stacking | ZOC | Combat |
| --- | --- | --- | --- | --- |
| **Deployed** | v0.1 rules | one per hex | exerts / obeys | normal |
| **Column** | fast, much faster on road/rail | stacks freely | exerts none; cannot enter enemy ZOC | cannot attack; fights badly |

- A column **auto-deploys** when it would enter enemy ZOC, is blocked, or is
  attacked, at a deterministic nearest-legal hex. Deploying costs tempo.
- Result: one-per-hex only binds where both sides are deployed — the front.
  Rear traffic stacks; garrisons in settlements stack up to a capacity.
- Tactical payoff: deploy too early and you crawl; too late and your column gets
  ambushed. Raiding columns and holding junctions become real jobs.

## 6. Map, sites and economy (proposal; resource list is open)

- **Objective density is the fix for sparseness.** Twin Rivers has ~1 objective
  per 200 hexes; unit density (0.5–2.5% of hexes) is probably fine to keep.
- **Ownership lives on sites, not every hex.** Provinces (~20–40 hexes) take
  their control and borders from an anchor site; the strategic map shows borders.
- **Resources: not decided.** Food, minerals, oil and factories were examples.
  The NetEase approach to resource variety is a good model. Test for any resource:
  *losing it must change what you can do next week*, not just shrink a number.
- **Sites unlock unit types (decided direction).** E.g. a **tank factory** enables
  armor. Every player wants one, so the map must supply enough that **most players
  who work for one can get one** — scarce enough to fight over, not so scarce that
  most players are locked out.
- Light supply (repair only when connected to friendly territory) is a probable
  later addition — it's what makes encirclement and rotation matter.

## 7. Army size, rotation, rear pressure (proposal)

- **Command cap grows with maturity:** a mature player fields ~12–15 brigades —
  enough for a front, flanks and a reserve. Warlords maybe slightly more.
- **Clicks per turn is the real limit.** Group/formation orders matter more than
  the cap number.
- **Rotation:** pull worn brigades (or a whole division) back to regroup and repair.
- **Unrest / marauders:** sites left uncovered accumulate unrest and eventually
  spawn bot raiders. This justifies home garrisons, gives early PvE, and soft-caps
  sprawl.

## 8. Players, factions, season arc (proposal)

- Everyone starts as a **warlord.** Some stay one (diplomacy with neighbors, a
  small edge, easy to team up against); most group into **factions** with chat
  and shared vision.
- **NPC governments** hold great cities: bot garrisons and fortifications too
  strong for one warlord — roughly five mature warlords to take one.
- Season arc: warlord land-grab → factions form → NPC cities fall → late-season
  objectives. Player-to-player chat comes eventually.

## 9. Bots are the product (decided)

- **First season is likely one human plus all-AI.** Bots aren't a stopgap that
  fills empty seats; they *are* the opponents, allies and factions.
- Layers: the existing tactical planner (`AI_SPEC.md`) commands units; an LLM
  strategic layer runs warlords/factions (diplomacy, objectives, assigning armies).
- A **headless world simulator** (bots only, turns as fast as compute allows) is
  both the bot test bench and the tool for tuning map size and density.

## 10. Proposed sequence

Each step keeps the current game playable and answers one question.

1. **Columns + auto-deploy + multi-turn travel** in the existing 2-player game.
   *Does packed transit kill traffic-jam play without killing tactics?*
2. **Sites + unit unlocks on a bigger map** (~80×80, 4 bots + human).
   *Does a map with reasons create more interesting play than "march at them"?*
3. **Headless world sim:** extend `ai/arena.py` to N bots, timed turns, and metrics
   (jams, front width, engagement rate, time-to-contact).
   *What size and density feel right?*
4. **Scale architecture:** chunked state instead of one JSONB row; per-turn
   resolution split into independent contact clusters (same pure engine);
   scheduled turns (Cloud Scheduler); province ↔ hex zoom in the renderer.
5. **Unrest/PvE, NPC cities, factions + chat, LLM commanders, season arc.**

## 11. Open questions

- The resource set, and how many types (§6).
- Whether, and when, to give humans standing orders or doctrine (§4).
- Column speed, deploy cost, and whether rail is a separate network (§5).
- Map size and player count for the first world.
- How to handle other time zones.
