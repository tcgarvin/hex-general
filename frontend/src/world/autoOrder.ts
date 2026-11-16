// Tap orders for the phone layout ("auto"): select a unit, press Move in the dock,
// and the next tap on the map picks the order, so the phone needs no Travel / Move
// mode chips. Without anything armed a tap never orders: it selects your unit
// there, or inspects the hex, so the player can look around first. The rule is
// pure (the planning hook gathers its inputs and carries out the result), so it is
// tested without React.
//
// The rule, first match wins:
//  1. nothing armed: a hex with one of your units selects it (the hook steps
//     through a stack); any other hex is inspected (the hook selects the hex and
//     lets the unit go);
//  2. "add a stop" or barrage armed, a hex with one of your units: select it;
//  3. "add a stop" armed: the tap becomes the travel's next waypoint;
//  4. barrage armed: shell the hex if in range, else say it is out of range and
//     stay armed;
//  5. Move armed, from here on as if none of your units stood on the hex: the
//     unit's own hex disarms;
//  6. a deployed gun tapping a visible hostile: guns can't attack (say so, use
//     Barrage);
//  7. a deployed unit tapping a hex in this turn's move reach: Move there (onto a
//     hostile, an attack that engages by default, `moveOrder`). The reach
//     ignores your own units where it must (`planMoveReach`): a Move into a
//     friend's way is staged, and the plan warns
//     (`planConflicts`) until the friend is ordered out; two units ordered into
//     each other's hex swap;
//  8. a deployed unit that a Travel would halt where it stands (it is inside the
//     contact zone, or its first step enters it), the hex beyond reach: no
//     Travel, since packing there halts at once, disordered. Move to the reachable,
//     non-hostile hex ending closest to the tap, when that is strictly closer, and
//     say so; else just say so;
//  9. otherwise Travel there, deploying on arrival (a previous travel's Stay
//     packed choice is kept). If the route runs into known contact, the travel
//     stops one hex short and deploys in order (`stopShort`) rather than halting
//     disordered. A column the route would halt where it stands gets no order;
// 10. no route (or off land): say so.
// Move is one shot: an order disarms it, a notice with nothing staged keeps it
// armed so the player can pick again. Nothing is sent until Submit.

import { axialDistance, axialEqual, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { haltHex, stopShort } from "./contact";
import { inBarrageRange, moveOrder, type WorldOrder } from "./plan";

type Unit = components["schemas"]["Unit"];
type StandingTravel = components["schemas"]["StandingTravel"];

/** What the next map tap is armed for, from the dock or the More sheet. */
export type Armed = "move" | "waypoint" | "barrage";

export const NO_ROUTE = "No route there";
export const TOWARD_IT = "Too far for this turn: moving toward it. Order again next turn.";
export const NOTHING_CLOSER = "Too far for this turn, and no move this turn gets closer. Try another hex.";
export const COLUMN_IN_CONTACT = "Too close to contact: a column would halt at once, disordered. Deploy it, then move.";
export const GUNS_CANNOT_ATTACK = "Guns can't attack. Use Barrage to shell it.";

export interface AutoTapInput {
  /** The selected unit (yours). */
  unit: Unit;
  hex: Axial;
  /** How many of your units stand on the tapped hex. */
  ownHere: number;
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
  /** Select your unit there (stepping through a stack). */
  | { kind: "select" }
  /** Nothing armed, none of your units there: look at the hex, letting the unit go. */
  | { kind: "inspect" }
  /** Move armed, the unit's own hex: disarm, keeping the selection. */
  | { kind: "disarm" }
  /** "Add a stop" armed: add the hex as a waypoint, as Shift+click does. */
  | { kind: "waypoint" }
  /** Stage `order` and disarm; show `notice` when it is not quite what was asked for. */
  | { kind: "order"; order: WorldOrder; notice: string | null }
  /** Nothing staged: say why, and arm the next tap for `armed`. */
  | { kind: "notice"; text: string; armed: Armed | null };

/** What a tap on `input.hex` does with the selected unit. */
export function autoTap(input: AutoTapInput): AutoTap {
  const { unit, hex } = input;
  if (input.armed === null) return input.ownHere > 0 ? { kind: "select" } : { kind: "inspect" };
  if (input.armed !== "move" && input.ownHere > 0) return { kind: "select" };
  if (input.armed === "waypoint") return { kind: "waypoint" };
  if (input.armed === "barrage") {
    const guns = unit.posture === "deployed" && unit.type === "artillery";
    if (guns && inBarrageRange(unit, hex) && !axialEqual(hex, unit.coord)) {
      return { kind: "order", order: { kind: "barrage", target: hex }, notice: null };
    }
    return { kind: "notice", text: input.outOfRange, armed: "barrage" };
  }
  if (axialEqual(hex, unit.coord)) return { kind: "disarm" };
  return moveTap(input);
}

/** Move armed, a hex other than the unit's own: rules 6 to 10 (see the header). */
function moveTap(input: AutoTapInput): AutoTap {
  const { unit, hex, reach, hostile } = input;
  const key = hexKey(hex.q, hex.r);
  const stayArmed = (text: string): AutoTap => ({ kind: "notice", text, armed: "move" });
  const deployed = unit.posture === "deployed";
  if (deployed && unit.type === "artillery" && hostile.has(key)) return stayArmed(GUNS_CANNOT_ATTACK);
  const path = reach.get(key);
  if (deployed && path !== undefined) return { kind: "order", order: moveOrder(path, hostile), notice: null };
  if (!input.passable) return stayArmed(NO_ROUTE);
  const route = input.route(hex);
  const halt = route === null ? null : haltHex(unit.coord, route, input.zone);
  const haltsAtOnce = halt !== null && axialEqual(halt, unit.coord);
  if (deployed && (input.zone.has(hexKey(unit.coord.q, unit.coord.r)) || haltsAtOnce)) {
    const toward = closerMove(unit.coord, hex, reach, hostile);
    if (toward === null) return stayArmed(NOTHING_CLOSER);
    return { kind: "order", order: moveOrder(toward, hostile), notice: TOWARD_IT };
  }
  if (route === null) return stayArmed(NO_ROUTE);
  if (haltsAtOnce) return stayArmed(COLUMN_IN_CONTACT);
  const previous = input.previous;
  const onArrival = previous?.kind === "travel" ? previous.onArrival : "deployed";
  const travel: WorldOrder = { kind: "travel", destination: hex, via: [], onArrival };
  if (halt === null) return { kind: "order", order: travel, notice: null };
  return { kind: "order", order: stopShort(travel, route, halt), notice: null };
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
