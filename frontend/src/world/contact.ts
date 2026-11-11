// Where a column would run into contact (WORLD_SPEC §4.5): a column halts and
// deploys, disordered, when it comes within the deploy distance of a hostile
// unit. The planner warns about it before a Travel order is given.

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];

/** `ColumnRules.deploy_distance` in `api/app/engine/stats.py`. */
export const DEPLOY_DISTANCE = 3;

/**
 * The hexes a column could bump into: every visible hostile unit, and every
 * neutral site still garrisoned (garrison tiers are on the public map, so they
 * count even out of sight).
 */
export function contactSources(state: WorldState, map: WorldMap, viewer: string): Axial[] {
  const held = new Map(state.settlements.map((s) => [hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null]));
  const sources = state.units.filter((u) => u.owner !== viewer).map((u) => u.coord);
  for (const site of map.sites) {
    if (site.garrison > 0 && (held.get(hexKey(site.coord.q, site.coord.r)) ?? null) === null) sources.push(site.coord);
  }
  return sources;
}

/** Every hex within `radius` of a source, so a route can be checked hex by hex. */
export function contactZone(sources: readonly Axial[], radius = DEPLOY_DISTANCE): Set<string> {
  const zone = new Set<string>();
  for (const c of sources) {
    for (let dq = -radius; dq <= radius; dq++) {
      for (let dr = Math.max(-radius, -dq - radius); dr <= Math.min(radius, -dq + radius); dr++) {
        zone.add(hexKey(c.q + dq, c.r + dr));
      }
    }
  }
  return zone;
}

/** Whether a column on `path` (from `origin`) would come within the zone. */
export function runsIntoContact(origin: Axial, path: readonly Axial[], zone: ReadonlySet<string>): boolean {
  return [origin, ...path].some((a) => zone.has(hexKey(a.q, a.r)));
}
