// Client-side mirror of the engine's visibility geometry (SPEC §9,
// `api/app/engine/visibility.py`), for turn playback.
//
// The server is the authority and fogs every payload: a replay carries only the
// events the viewer witnessed and the units visible at the END of the turn.
// Playback reconstructs the board pulse by pulse, so it needs the viewer's sight
// at each beat too: to fade in an enemy the moment the viewer's army comes
// within sight of it, to fade out one the army has left behind, and to draw the
// fog veil per beat. This mirror recomputes it from the viewer's own units
// (which the viewer always knows) and the sites they control. It is exact
// against the engine (`visibility.test.ts` checks it on recorded turns).
//
// Rules mirrored: a unit sees every on-map hex within its vision radius with a
// clear sightline (woods strictly between the endpoints block it; the endpoints
// never do); a controlled settlement sees radius 1 with no sightline check.

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

type Terrain = components["schemas"]["Terrain"];
type UnitType = components["schemas"]["UnitType"];

/** Terrain at an axial hex, `undefined` off the map (e.g. `WorldMap.hexAt(q, r)?.terrain`). */
export type TerrainAt = (q: number, r: number) => Terrain | undefined;

/**
 * Sight radius per unit type (`UNIT_STATS[...].vision` in api/app/engine/stats.py).
 * Kept here rather than read from `planning/rules.ts`, whose mirror still says 2
 * (the engine moved to 3); the fixture parity test pins this to the engine.
 */
export const VISION: Record<UnitType, number> = { infantry: 3, armor: 3, artillery: 3 };

/** Terrain that blocks a sightline passing through it (`TERRAIN_STATS[...].blocks_line_of_sight`). */
function blocksSight(terrain: Terrain): boolean {
  return terrain === "woods";
}

/** Cube rounding exactly as `hex_coords._cube_round` (x = q, z = r, y = -q - r). */
function cubeRound(x: number, y: number, z: number): Axial {
  let rx = Math.round(x);
  let ry = Math.round(y);
  let rz = Math.round(z);
  const dx = Math.abs(rx - x);
  const dy = Math.abs(ry - y);
  const dz = Math.abs(rz - z);
  if (dx > dy && dx > dz) rx = -ry - rz;
  else if (dy > dz) ry = -rx - rz;
  else rz = -rx - ry;
  return { q: rx, r: rz };
}

function distance(a: Axial, b: Axial): number {
  const dq = a.q - b.q;
  const dr = a.r - b.r;
  return (Math.abs(dq) + Math.abs(dq + dr) + Math.abs(dr)) / 2;
}

/**
 * The hexes on the line from `a` to `b` inclusive, sampled with the engine's
 * epsilon nudge (`hex_coords.line`) so vertex-grazing lines resolve identically.
 */
export function hexLine(a: Axial, b: Axial): Axial[] {
  const steps = distance(a, b);
  if (steps === 0) return [a];
  const ax = a.q;
  const az = a.r;
  const ay = -ax - az;
  const bx = b.q;
  const bz = b.r;
  const by = -bx - bz;
  const out: Axial[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    out.push(cubeRound(ax + (bx - ax) * t + 1e-6, ay + (by - ay) * t + 1e-6, az + (bz - az) * t - 2e-6));
  }
  return out;
}

function hasLineOfSight(terrainAt: TerrainAt, from: Axial, to: Axial): boolean {
  const line = hexLine(from, to);
  for (let i = 1; i < line.length - 1; i++) {
    const between = terrainAt(line[i].q, line[i].r);
    if (between !== undefined && blocksSight(between)) return false;
  }
  return true;
}

function forDisc(center: Axial, radius: number, visit: (c: Axial) => void): void {
  for (let dq = -radius; dq <= radius; dq++) {
    for (let dr = Math.max(-radius, -dq - radius); dr <= Math.min(radius, -dq + radius); dr++) {
      visit({ q: center.q + dq, r: center.r + dr });
    }
  }
}

/** Radius a controlled settlement sees (`visibility._SETTLEMENT_VISION`). */
const SETTLEMENT_VISION = 1;

export interface Observer {
  owner: string;
  type: UnitType;
  coord: Axial;
}

export interface ControlledSite {
  coord: Axial;
  controlled_by?: string | null;
}

/**
 * Keys (`hexKey`) of every hex `viewer` sees, from their units and the
 * settlements they control. Mirrors `visible_hexes`.
 */
export function visibleHexKeys(
  units: Iterable<Observer>,
  settlements: Iterable<ControlledSite>,
  viewer: string,
  terrainAt: TerrainAt,
): Set<string> {
  const seen = new Set<string>();
  for (const unit of units) {
    if (unit.owner !== viewer) continue;
    forDisc(unit.coord, VISION[unit.type], (c) => {
      const key = hexKey(c.q, c.r);
      if (seen.has(key) || terrainAt(c.q, c.r) === undefined) return;
      if (hasLineOfSight(terrainAt, unit.coord, c)) seen.add(key);
    });
  }
  for (const s of settlements) {
    if (s.controlled_by !== viewer) continue;
    forDisc(s.coord, SETTLEMENT_VISION, (c) => {
      if (terrainAt(c.q, c.r) !== undefined) seen.add(hexKey(c.q, c.r));
    });
  }
  return seen;
}
