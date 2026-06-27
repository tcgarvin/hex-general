# TODO

Running list of follow-ups and tech debt that don't belong to a specific roadmap
phase. Newest section on top. See [`ROADMAP.md`](./ROADMAP.md) for planned feature
work.

**When an item is finished, delete it from this file — don't check it off.** This
list tracks only outstanding work; completed work lives in git history.

## Phase 10 replay — review follow-ups

From a code review of the Phase 10 commits (replay: beat engine + per-beat board
state + animation overlays).

- [ ] **Confirm replay auto-navigation can't interrupt planning / drop staged orders.**
  `frontend/src/routes/GameShell.tsx` calls `navigate()` to the replay route from a
  background poll when it detects a turn advance, unmounting the planning shell.
  Verify this can't yank a player out mid-planning and discard staged-but-unlocked
  orders.

- [ ] **Minor cleanups.** Two `react-hooks/exhaustive-deps` suppressions
  (`usePlayback` mount-only effect; `ReplayShell` `[currentBeat]`-only effect that
  also reads `speed`/`beats`/`preTurnState`) work only because those deps are
  stable per replay — revisit if that assumption ever changes.
