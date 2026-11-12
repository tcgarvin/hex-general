// Where a column would run into contact (WORLD_SPEC §4.5): a column halts and
// deploys, disordered, when it comes within the deploy distance of a hostile
// unit. The planner warns about it before a Travel order is given, marks where a
// staged or standing travel would halt, and flags the route hexes out of sight.

import { axialEqual, axialNeighbors, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { at } from "./waypoints";
import type { WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = components["schemas"]["Unit"];

/** `ColumnRules.deploy_distance` in `api/app/engine/stats.py`. */
export const DEPLOY_DISTANCE = 3;

/**
 * The lowest garrison tier with more than one unit (WORLD_SPEC §7: tier 3 is an
 * infantry and a gun). The first unit stands on the site and the rest beside it,
 * so such a garrison can be met from a hex one step further out.
 */
export const SPREAD_GARRISON_TIER = 3;

/**
 * The hexes a column could bump into: every visible hostile unit, and every
 * neutral site still garrisoned (garrison tiers are on the public map, so they
 * count even out of sight), with the hexes beside it for a garrison of several
 * units.
 */
export function contactSources(state: WorldState, map: WorldMap, viewer: string): Axial[] {
  const held = new Map(state.settlements.map((s) => [hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null]));
  const sources = state.units.filter((u) => u.owner !== viewer).map((u) => u.coord);
  for (const site of map.sites) {
    if (site.garrison <= 0 || (held.get(hexKey(site.coord.q, site.coord.r)) ?? null) !== null) continue;
    sources.push(site.coord);
    if (site.garrison >= SPREAD_GARRISON_TIER) sources.push(...axialNeighbors(site.coord));
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

/**
 * Where a column on `path` (from `origin`) would halt on contact with the known
 * zone: the last route hex, origin included, before the first one inside it, or
 * the origin itself when it already stands inside. `null`: it never runs into
 * the zone. The engine halts a column at the start of the pulse whose next step
 * would come within the deploy distance, so it stops one hex short of the zone.
 */
export function haltHex(origin: Axial, path: readonly Axial[], zone: ReadonlySet<string>): Axial | null {
  const route = [origin, ...path];
  const first = route.findIndex((a) => zone.has(hexKey(a.q, a.r)));
  if (first < 0) return null;
  return route[Math.max(0, first - 1)];
}

/** The halt in prose: "halts on contact at (3,4), disordered". */
export function haltText(halt: Axial, origin: Axial): string {
  return axialEqual(halt, origin)
    ? "halts on contact where it stands, disordered"
    : `halts on contact at ${at(halt)}, disordered`;
}

/**
 * The default plan mode for a selected unit: Move for a deployed unit already
 * inside the contact zone (a Travel would pack it, then halt it at once,
 * disordered), else Travel.
 */
export function defaultPlanMode(unit: Pick<Unit, "coord" | "posture">, zone: ReadonlySet<string>): "travel" | "move" {
  return unit.posture === "deployed" && zone.has(hexKey(unit.coord.q, unit.coord.r)) ? "move" : "travel";
}

/**
 * The travel order rewritten to stop at `halt` (a hex on `path`) and deploy
 * there by choice, so it arrives in order instead of halting disordered. Only
 * the waypoints met on `path` before the halt are kept.
 */
export function stopShort(
  order: { destination: Axial; via: readonly Axial[] },
  path: readonly Axial[],
  halt: Axial,
): { kind: "travel"; destination: Axial; via: Axial[]; onArrival: "deployed" } {
  const stop = path.findIndex((a) => axialEqual(a, halt));
  if (stop < 0) throw new Error(`halt ${at(halt)} is not on the route`);
  const via: Axial[] = [];
  let from = 0;
  for (const w of order.via) {
    const i = path.findIndex((a, k) => k >= from && axialEqual(a, w));
    if (i < 0 || i >= stop) break;
    via.push(w);
    from = i + 1;
  }
  return { kind: "travel", destination: halt, via, onArrival: "deployed" };
}

/** Which hexes of a route the viewer cannot see this turn. */
export interface RouteFog {
  /** Per hex of the path (origin excluded): not in view. */
  unscouted: boolean[];
  count: number;
  /** The unscouted hexes are all at the end of the route. */
  trailing: boolean;
}

export function routeFog(path: readonly Axial[], visible: ReadonlySet<string>): RouteFog {
  const unscouted = path.map((a) => !visible.has(hexKey(a.q, a.r)));
  const count = unscouted.filter(Boolean).length;
  const firstHidden = unscouted.indexOf(true);
  const trailing = count > 0 && unscouted.slice(firstHidden).every(Boolean);
  return { unscouted, count, trailing };
}

/** The fog of a route treated as wholly in view (a deployed Move, whose fog isn't shown). */
export function inView(path: readonly Axial[]): RouteFog {
  return { unscouted: path.map(() => false), count: 0, trailing: false };
}

/** The fog warning for a route, or `null` when all of it is in view. */
export function fogText(fog: RouteFog): string | null {
  const n = fog.count;
  if (n === 0) return null;
  const where =
    n === fog.unscouted.length
      ? n === 1
        ? "The destination is unscouted"
        : `All ${n} hexes of the route are unscouted`
      : fog.trailing
        ? n === 1
          ? "Last hex unscouted"
          : `Last ${n} hexes unscouted`
        : n === 1
          ? "1 hex of the route is unscouted"
          : `${n} hexes of the route are unscouted`;
  return `${where}: a column that meets something hidden there halts, disordered.`;
}
