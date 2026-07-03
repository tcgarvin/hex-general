# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

**When an item is finished, delete it from this file — don't check it off.** This
list tracks only outstanding work; completed work lives in git history.

## Engine: own mooted-intent semantics, then retire `resolve_plan`

Arena play (AI_SPEC work) surfaced two crash-level gaps in `resolve_execution`,
both reachable in normal human-vs-human games. As a stopgap they are handled in
`api/app/ai/simulate.py` (`resolve_plan`), which the game service and the AI now
resolve turns through — but that leaves turn semantics defined *outside* the
engine, and `_viable_recruitments` duplicates `resolve_recruitment`'s dynamic
checks (drift hazard). Fix at the source:

- **Orders for barrage-killed units.** Barrage removes destroyed units before
  movement (SPEC §11.3), so a statically-legal Move/Hold/Rest order can name a
  unit that no longer exists; `resolve_movement` raises `EngineError`. Make the
  engine treat "ordered unit died in barrage" as defined simultaneity (skip the
  order, ideally with an event for the replay), keeping the crash for orders
  naming units that *never* existed.
- **Recruitments voided mid-turn.** Recruitment resolves last, so a
  submission-time-legal recruit can be voided by the turn itself: the city was
  captured (possibly by a fog-hidden enemy), a unit ended its move on the hex,
  or the post-economy bank fell short. Engine should skip the voided request
  (muster stays banked, event emitted) rather than raise.
- Then delete `resolve_plan`'s duplicate protections and point the service,
  arena, and planner back at `resolve_execution` (or reduce `resolve_plan` to a
  pure re-export). Move/adapt the tests in `api/tests/ai/` that cover the drop
  behaviour into `api/tests/engine/`.

## Frontend: new game vs. human *or* bot

The API side shipped (AI_SPEC §9): `POST /games` accepts `vs_bot: bool` and
`bot_policy` (`"scripted"` / `"search"`, registry in `app/ai/arena.py`), fills
the open slot with a bot, and the game starts immediately — the bot plans
synchronously when the human submits. Types are already regenerated in
`schema.d.ts`. Remaining work is all frontend UX:

- New-game flow offers an opponent choice: invite a human (current flow) or
  play the computer (pick a difficulty/policy — scripted is the more aggressive
  opponent until the search bot's tuning lands).
- Vs-bot games skip the invite/lobby wait state — the game is active from
  creation, so route straight to planning.
- Sanity-pass the dispatch/history screens for bot games (opponent display
  name, resign, outcome labels).
