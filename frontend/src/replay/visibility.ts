// Client-side mirror of the engine's visibility geometry
// (`api/app/engine/visibility.py`, SPEC §9). The server stays the authority and
// fog-filters every payload before it leaves the API; this mirror exists because
// a replay reconstructs each beat's board state locally (`applyBeats`), so the
// matching fog for that reconstructed state must be computed locally too — the
// turn payload carries no per-beat visible set.
//
// Rules mirrored: binary visibility (SPEC §9.1) — a player sees a hex if any of
// their units has a clear sightline to it within that unit's vision radius
// (SPEC §9.3); woods block a sightline but are themselves visible at its end
// (SPEC §9.2); controlled settlements contribute a fixed radius of 1 with no
// line-of-sight check.

import { axialDistance, axialLine, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import type { GameState, PlayerId } from "@/lib/api/types";
import { blocksLineOfSight, UNIT_STATS } from "@/planning/rules";

type Terrain = components["schemas"]["Terrain"];

/** Terrain by hex key, the lookup a sightline walks. */
export type TerrainIndex = ReadonlyMap<string, Terrain>;

export function terrainIndex(state: GameState): TerrainIndex {
  const byKey = new Map<string, Terrain>();
  for (const hex of state.map.hexes) {
    byKey.set(hexKey(hex.coord.q, hex.coord.r), hex.terrain);
  }
  return byKey;
}

/**
 * Whether `source` has a clear sightline to `target` (SPEC §9.2): blocked iff
 * some hex *strictly between* the endpoints blocks line of sight. The endpoints
 * themselves never block — an observer sees out of its own woods hex and into a
 * woods hex it looks directly at.
 */
export function hasLineOfSight(terrain: TerrainIndex, source: Axial, target: Axial): boolean {
  const sightline = axialLine(source, target);
  for (const coord of sightline.slice(1, -1)) {
    const between = terrain.get(hexKey(coord.q, coord.r));
    if (between !== undefined && blocksLineOfSight(between)) return false;
  }
  return true;
}

/** Radius controlled settlements see, unit or no unit (visibility.py). */
const SETTLEMENT_VISION = 1;

/**
 * All hexes `playerId` can see in `state` — the union over their units' vision
 * (LOS-checked) and their controlled settlements' fixed radius. Mirrors the
 * engine's `visible_hexes`; feeds the board's fog veil during replay.
 */
export function visibleHexes(state: GameState, playerId: PlayerId): Axial[] {
  const terrain = terrainIndex(state);
  const visible = new Map<string, Axial>();

  for (const unit of state.units ?? []) {
    if (unit.owner !== playerId) continue;
    const vision = UNIT_STATS[unit.type].vision;
    for (const hex of state.map.hexes) {
      const coord = hex.coord;
      const key = hexKey(coord.q, coord.r);
      if (visible.has(key)) continue;
      if (axialDistance(unit.coord, coord) > vision) continue;
      if (hasLineOfSight(terrain, unit.coord, coord)) visible.set(key, coord);
    }
  }

  for (const settlement of state.settlements ?? []) {
    if (settlement.controlled_by !== playerId) continue;
    for (const hex of state.map.hexes) {
      const coord = hex.coord;
      const key = hexKey(coord.q, coord.r);
      if (!visible.has(key) && axialDistance(settlement.coord, coord) <= SETTLEMENT_VISION) {
        visible.set(key, coord);
      }
    }
  }

  return [...visible.values()];
}
