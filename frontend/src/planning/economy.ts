// Muster economy derivation for the header/Control panel (SPEC §6). The engine is
// authoritative; this recomputes the same arithmetic client-side so the UI can
// show a player's bank, net income, and per-settlement yield without an extra
// round-trip. Net income is *gross settlement income − unit upkeep*, the amount
// added to the bank (capped at `muster_bank_cap`) each turn.

import { hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { SETTLEMENT_INCOME, UNIT_STATS } from "./rules";

type GameState = components["schemas"]["GameState"];
type Terrain = components["schemas"]["Terrain"];

export interface Economy {
  /** Current muster bank for the slot (`Player.bank`). */
  bank: number;
  /** Bank ceiling (`GameConfig.muster_bank_cap`); income past it is lost. */
  cap: number;
  /** Gross settlement income per turn. */
  gross: number;
  /** Total unit upkeep per turn. */
  upkeep: number;
  /** Net change applied to the bank each turn (`gross − upkeep`). */
  net: number;
  /** Settlement counts controlled by the slot. */
  cities: number;
  towns: number;
}

/** Income a single settlement hex yields (0 for non-settlement terrain). */
export function settlementIncome(terrain: Terrain): number {
  return SETTLEMENT_INCOME[terrain] ?? 0;
}

/** Derive the muster economy for `slot` from a (fog-public) game state. */
export function economyFor(state: GameState, slot: string): Economy {
  const terrainAt = new Map<string, Terrain>();
  for (const h of state.map.hexes ?? []) {
    terrainAt.set(hexKey(h.coord.q, h.coord.r), h.terrain);
  }

  let gross = 0;
  let cities = 0;
  let towns = 0;
  for (const s of state.settlements ?? []) {
    if (s.controlled_by !== slot) continue;
    const terrain = terrainAt.get(hexKey(s.coord.q, s.coord.r));
    if (terrain === "city") cities += 1;
    else if (terrain === "town") towns += 1;
    if (terrain !== undefined) gross += settlementIncome(terrain);
  }

  let upkeep = 0;
  for (const u of state.units ?? []) {
    if (u.owner === slot) upkeep += UNIT_STATS[u.type].upkeep;
  }

  const player = (state.players ?? []).find((p) => p.id === slot);
  return {
    bank: player?.bank ?? 0,
    cap: state.config.muster_bank_cap,
    gross,
    upkeep,
    net: gross - upkeep,
    cities,
    towns,
  };
}
