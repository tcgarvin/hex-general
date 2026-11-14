// One-tap orders ("auto"): with one of your units selected, a tap on a hex picks
// the order, so the phone layout needs no Travel / Move / Barrage mode chips. The
// rule is pure (the planning hook gathers its inputs and carries out the result),
// so the desktop shell can adopt it later and it is tested without React.
//
// The rule, first match wins:
//  1. a hex with one of your units selects it (the hook steps through a stack);
//  2. "add a stop" armed: the tap becomes the travel's next waypoint;
//  3. barrage armed: shell the hex if in range, else say it is out of range;
//  4. deployed artillery tapping a visible hostile in range: barrage it;
//  5. a deployed unit tapping a hex in this turn's move reach: Move there (onto a
//     hostile, an attack);
//  6. a deployed unit that a Travel would halt where it stands (it is inside the
//     contact zone, or its first step enters it), the hex beyond reach: no
//     Travel, since packing there halts at once, disordered. Move to the reachable,
//     non-hostile hex ending closest to the tap, when that is strictly closer, and
//     say so; else just say so;
//  7. otherwise Travel there, deploying on arrival (a previous travel's Stay
//     packed choice is kept). If the route runs into known contact, the travel
//     stops one hex short and deploys in order (`stopShort`) rather than halting
//     disordered. A column the route would halt where it stands gets no order;
//  8. no route (or off land): say so, and keep the selection.
// A tap stages the order straight away; nothing is sent until Submit.

import { axialDistance, axialEqual, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { haltHex, stopShort } from "./contact";
import { inBarrageRange, type WorldOrder } from "./plan";

type Unit = components["schemas"]["Unit"];
type StandingTravel = components["schemas"]["StandingTravel"];

/** What the next map tap is armed for, from the dock or the More sheet. */
export type Armed = "waypoint" | "barrage";

export const NO_ROUTE = "No route there";
export const TOWARD_IT = "Too far for this turn: moving toward it. Order again next turn.";
export const NOTHING_CLOSER = "Too far for this turn, and no move this turn gets closer. Try another hex.";
export const COLUMN_IN_CONTACT = "Too close to contact: a column would halt at once, disordered. Deploy it, then move.";

export interface AutoTapInput {
  /** The selected unit (yours). */
  unit: Unit;
  hex: Axial;
  /** The tapped hex holds one of your units. */
  ownHere: boolean;
  armed: Armed | null;
  /** The unit's fresh move reach this turn (`moveReach`; empty for a column). */
  reach: ReadonlyMap<string, readonly Axial[]>;
  /** Hexes holding a visible hostile unit (`hostileHexes`). */
  hostile: ReadonlySet<string>;
  /** The known contact zone (`contactZone`). */
  zone: ReadonlySet<string>;
  /** The tapped hex is on the map and passable. */
  passable: boolean;
  /** The column route from the unit to `to` (no waypoints), or `null`: none. */
  route: (to: Axial) => readonly Axial[] | null;
  /** The unit's staged order, if any. */
  previous: WorldOrder | undefined;
  /** The out-of-range notice (`OUT_OF_RANGE`). */
  outOfRange: string;
}

export type AutoTap =
  /** Rule 1: select the unit there (stepping through a stack). */
  | { kind: "select" }
  /** Rule 2: add the hex as a waypoint, as Shift+click does. */
  | { kind: "waypoint" }
  /** Stage `order`; show `notice` when it is not quite what was asked for. */
  | { kind: "order"; order: WorldOrder; notice: string | null; armed: Armed | null }
  /** Nothing staged: say why. */
  | { kind: "notice"; text: string; armed: Armed | null };

/** The order (or notice) a tap on `input.hex` gives the selected unit. */
export function autoTap(input: AutoTapInput): AutoTap {
  const { unit, hex, reach, hostile } = input;
  const key = hexKey(hex.q, hex.r);
  if (input.ownHere) return { kind: "select" };
  if (input.armed === "waypoint") return { kind: "waypoint" };
  const deployed = unit.posture === "deployed";
  const guns = deployed && unit.type === "artillery";
  if (input.armed === "barrage") {
    if (guns && inBarrageRange(unit, hex) && !axialEqual(hex, unit.coord)) {
      return { kind: "order", order: { kind: "barrage", target: hex }, notice: null, armed: null };
    }
    return { kind: "notice", text: input.outOfRange, armed: "barrage" };
  }
  if (guns && hostile.has(key) && inBarrageRange(unit, hex)) {
    return { kind: "order", order: { kind: "barrage", target: hex }, notice: null, armed: null };
  }
  const path = reach.get(key);
  if (deployed && path !== undefined) {
    return { kind: "order", order: { kind: "move", path: [...path] }, notice: null, armed: null };
  }
  if (!input.passable) return { kind: "notice", text: NO_ROUTE, armed: null };
  const route = input.route(hex);
  const halt = route === null ? null : haltHex(unit.coord, route, input.zone);
  const haltsAtOnce = halt !== null && axialEqual(halt, unit.coord);
  if (deployed && (input.zone.has(hexKey(unit.coord.q, unit.coord.r)) || haltsAtOnce)) {
    const toward = closerMove(unit.coord, hex, reach, hostile);
    if (toward === null) return { kind: "notice", text: NOTHING_CLOSER, armed: null };
    return { kind: "order", order: { kind: "move", path: [...toward] }, notice: TOWARD_IT, armed: null };
  }
  if (route === null) return { kind: "notice", text: NO_ROUTE, armed: null };
  if (haltsAtOnce) return { kind: "notice", text: COLUMN_IN_CONTACT, armed: null };
  const previous = input.previous;
  const onArrival = previous?.kind === "travel" ? previous.onArrival : "deployed";
  const travel: WorldOrder = { kind: "travel", destination: hex, via: [], onArrival };
  if (halt === null) return { kind: "order", order: travel, notice: null, armed: null };
  return { kind: "order", order: stopShort(travel, route, halt), notice: null, armed: null };
}

/**
 * The move in `reach` that ends closest to `target`, not on a hostile hex, and
 * strictly closer than `from`; ties go to the shorter path, then the first
 * found. `null`: no move gets closer.
 */
export function closerMove(
  from: Axial,
  target: Axial,
  reach: ReadonlyMap<string, readonly Axial[]>,
  hostile: ReadonlySet<string>,
): readonly Axial[] | null {
  let best: { path: readonly Axial[]; d: number } | null = null;
  for (const [key, path] of reach) {
    if (hostile.has(key) || path.length === 0) continue;
    const d = axialDistance(path[path.length - 1], target);
    if (best === null || d < best.d || (d === best.d && path.length < best.path.length)) best = { path, d };
  }
  return best !== null && best.d < axialDistance(from, target) ? best.path : null;
}

/**
 * The units "Hold all" orders to Hold: your deployed units with nothing to do
 * this turn (no staged order and no standing travel). A column is left out: Hold
 * gives a column no defence bonus (WORLD_SPEC §4.2), and one with nothing to do
 * already stays put, so the order would change nothing.
 */
export function holdAllUnits(
  units: readonly Unit[],
  orders: Readonly<Record<number, WorldOrder>>,
  standing: readonly StandingTravel[],
): Unit[] {
  const travelling = new Set(standing.map((s) => s.unit_id));
  return units.filter((u) => u.posture === "deployed" && orders[u.id] === undefined && !travelling.has(u.id));
}
