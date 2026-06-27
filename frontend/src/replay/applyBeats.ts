// Pure function that reconstructs the board state at any beat boundary during
// replay (UI-SPEC §13.1, ROADMAP Phase 10 Slice 3). This is the foundation for
// per-beat board state and step-back (re-render from the computed snapshot, not
// reverse animation).
//
// Each call is O(beats × events × units), which is small for a v0.1 game
// (~40 units, ≤~200 events per turn). The output is safe to pass directly to the
// Board as `state`.

import type { components } from "@/lib/api/schema";
import type { ReplayEvent } from "@/lib/api/types";
import { UNIT_STATS } from "@/planning/rules";

import type { Beat } from "./beats";

type GameState = components["schemas"]["GameState"];
type Unit = components["schemas"]["Unit"];
type Settlement = components["schemas"]["Settlement"];
type Player = components["schemas"]["Player"];

/**
 * Compute the board state at the boundary of beat `upToIndex` by applying all
 * events in beats 0..upToIndex (inclusive) to `preTurnState`. Returns
 * `preTurnState` unchanged when `upToIndex < 0` (pre-turn state).
 */
export function applyBeats(
  preTurnState: GameState,
  beats: Beat[],
  upToIndex: number,
): GameState {
  if (upToIndex < 0) return preTurnState;
  let state = preTurnState;
  const limit = Math.min(upToIndex + 1, beats.length);
  for (let i = 0; i < limit; i++) {
    state = applyBeatEvents(state, beats[i].events);
  }
  return state;
}

/** Apply all events from one beat to `state`, returning the updated state. */
export function applyBeatEvents(state: GameState, events: ReplayEvent[]): GameState {
  let units = (state.units ?? []) as Unit[];
  let settlements = (state.settlements ?? []) as Settlement[];
  let players = (state.players ?? []) as Player[];
  let changed = false;

  for (const e of events) {
    const next = applyOneEvent(e, units, settlements, players);
    if (next.units !== units || next.settlements !== settlements || next.players !== players) {
      ({ units, settlements, players } = next);
      changed = true;
    }
  }

  return changed ? { ...state, units, settlements, players } : state;
}

function applyOneEvent(
  e: ReplayEvent,
  units: Unit[],
  settlements: Settlement[],
  players: Player[],
): { units: Unit[]; settlements: Settlement[]; players: Player[] } {
  const unchanged = { units, settlements, players };

  switch (e.kind) {
    case "unit_moved": {
      const { unit_id, dest } = e;
      return { units: units.map((u) => (u.id === unit_id ? { ...u, coord: dest } : u)), settlements, players };
    }

    case "barrage_resolved": {
      const { hit } = e;
      // `hit` is null/absent when the barrage hit an empty hex (SPEC §7.2).
      if (hit == null) return unchanged;
      if (hit.destroyed) {
        return { units: units.filter((u) => u.id !== hit.unit_id), settlements, players };
      }
      return {
        units: units.map((u) => (u.id === hit.unit_id ? { ...u, hp: hit.hp_after } : u)),
        settlements,
        players,
      };
    }

    case "combat_resolved": {
      const { coord, participants, entrant_id } = e;
      let newUnits = units;
      for (const p of participants) {
        if (p.destroyed) {
          newUnits = newUnits.filter((u) => u.id !== p.unit_id);
        } else {
          newUnits = newUnits.map((u) => (u.id === p.unit_id ? { ...u, hp: p.hp_after } : u));
        }
      }
      // The surviving entrant now occupies the contested hex (null/absent when
      // nobody entered — opposing survivors remained or all entrants died).
      if (entrant_id != null) {
        newUnits = newUnits.map((u) => (u.id === entrant_id ? { ...u, coord } : u));
      }
      return { units: newUnits, settlements, players };
    }

    case "settlement_captured": {
      const { coord, captured_by } = e;
      return {
        units,
        settlements: settlements.map((s) =>
          s.coord.q === coord.q && s.coord.r === coord.r
            ? { ...s, controlled_by: captured_by }
            : s,
        ),
        players,
      };
    }

    case "unit_repaired": {
      const { unit_id, hp_after } = e;
      return {
        units: units.map((u) => (u.id === unit_id ? { ...u, hp: hp_after } : u)),
        settlements,
        players,
      };
    }

    case "economy_resolved": {
      const { player, bank_after } = e;
      return {
        units,
        settlements,
        players: players.map((p) => (p.id === player ? { ...p, bank: bank_after } : p)),
      };
    }

    case "unit_recruited": {
      const { unit_id, unit_type: type, owner, coord } = e;
      const newUnit: Unit = {
        id: unit_id,
        type,
        owner,
        coord,
        // The unit_recruited event carries no HP; a unit enters play at full HP.
        hp: UNIT_STATS[type].maxHp,
      };
      return { units: [...units, newUnit], settlements, players };
    }

    // move_blocked, traffic_jam, zoc_control_stop: unit positions unchanged.
    default:
      return unchanged;
  }
}
