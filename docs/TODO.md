# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

## Phase 10 replay — review follow-ups

From a code review of the Phase 10 commits (replay: beat engine + per-beat board
state + animation overlays).

- [ ] **Type `TurnReplay.events` as the engine `Event` union (API-side root fix).**
  `api/app/games/schemas.py` types `events: list[dict[str, Any]]`, so `gen:api`
  emits `{[key: string]: unknown}[]` and the frontend hand-casts events ~15× via
  `as unknown as {…}` (`applyBeats.ts`, `ReplayShell.tsx` `extractAnimData`,
  `beats.ts`). These casts bypass type-checking — an API field rename (e.g.
  `dest`→`to`) would silently break replay. The engine already has a typed `Event`
  discriminated union (`api/app/engine/events.py`); plumb it through the response
  model so the union propagates to the frontend and the casts can be deleted.

- [ ] **Confirm replay auto-navigation can't interrupt planning / drop staged orders.**
  `frontend/src/routes/GameShell.tsx` calls `navigate()` to the replay route from a
  background poll when it detects a turn advance, unmounting the planning shell.
  Verify this can't yank a player out mid-planning and discard staged-but-unlocked
  orders.

- [ ] **Minor cleanups.** `useReplay` fetches turn N then N-1 sequentially — could
  be `Promise.all`. Two `react-hooks/exhaustive-deps` suppressions (`usePlayback`
  mount-only effect; `ReplayShell` `[currentBeat]`-only effect that also reads
  `speed`/`beats`/`preTurnState`) work only because those deps are stable per
  replay — revisit if that assumption ever changes.
