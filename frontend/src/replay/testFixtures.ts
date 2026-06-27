// Shared test fixtures for the replay beat-engine unit tests. Not a test file
// itself (vitest only collects `*.test.ts`), so it can hold builders the
// `beats.test.ts` / `applyBeats.test.ts` suites import.

import type { components } from "@/lib/api/schema";
import type { ReplayEvent } from "@/lib/api/types";

type GameState = components["schemas"]["GameState"];
type Unit = components["schemas"]["Unit"];
type Settlement = components["schemas"]["Settlement"];
type Player = components["schemas"]["Player"];
type UnitType = components["schemas"]["UnitType"];
type Axial = components["schemas"]["Axial"];

// A terse event fixture: a real event's `kind` plus only the fields the reducer
// under test reads (the engine emits more, but the beat reducers ignore them).
export type EventFixture = { kind: string; [key: string]: unknown };

/**
 * Cast terse {@link EventFixture}s to the strict {@link ReplayEvent} union for the
 * reducers. This is the one place the tests cross from loose fixtures into the
 * typed log; production code never casts (it consumes the typed union directly).
 */
export function events(fixtures: EventFixture[]): ReplayEvent[] {
  return fixtures as unknown as ReplayEvent[];
}

export function coord(q: number, r: number): Axial {
  return { q, r };
}

export function unit(
  id: number,
  owner: string,
  c: Axial,
  opts: { type?: UnitType; hp?: number } = {},
): Unit {
  return { id, owner, coord: c, type: opts.type ?? "infantry", hp: opts.hp ?? 10 };
}

export function settlement(c: Axial, controlledBy: string | null = null): Settlement {
  return { coord: c, controlled_by: controlledBy };
}

export function player(id: string, bank = 0): Player {
  return { id, bank };
}

/**
 * Build a minimal-but-valid {@link GameState}. The replay reducers only ever
 * touch `units`/`settlements`/`players`, but the type requires `config`/`map`,
 * so we supply trivial placeholders.
 */
export function gameState(parts: {
  turn?: number;
  units?: Unit[];
  settlements?: Settlement[];
  players?: Player[];
}): GameState {
  return {
    turn: parts.turn ?? 1,
    config: {
      muster_bank_cap: 20,
      unit_cap: 20,
      victory: {
        city_elimination: true,
        city_points: 3,
        town_points: 1,
        turn_limit: 30,
      },
    },
    map: { width: 4, height: 4, hexes: [] },
    units: parts.units ?? [],
    settlements: parts.settlements ?? [],
    players: parts.players ?? [],
  };
}
