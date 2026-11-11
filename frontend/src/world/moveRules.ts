// Which hexes a deployed unit's Move can step through or end on this turn, given
// the units the player can see and the rest of the plan. A port of the parts of
// the engine's pulse loop (`api/app/engine/movement.py`) that a planner can
// predict:
//
// * **Hostile units** (any other owner, neutral garrisons included): stepping
//   into their hex is an attack (`_classify`). It ends the planned path, though
//   the engine lets a winner carry on: a plan can't count on winning. Artillery
//   and disordered units can't attack at all (`ATTACK_FORBIDDEN`, `DISORDERED`).
//   A hex of hostile columns counts too: they deploy on contact before the step.
// * **ZOC** (SPEC §8.4): a unit that starts a pulse next to a hostile *deployed*
//   unit may not step into another hex next to one (`ZOC_LOCKED`), except to
//   attack. Columns exert no ZOC.
// * **Own deployed units** hold their hex (one deployed unit per hex): a step
//   into one lands only once it has left (`_resolve_follow_chains`, else
//   `OCCUPIED`). One with a Move leaves on pulse 1 (assumed to succeed; the
//   friendly swaps of WORLD_SPEC §4.7 also resolve then). One with a Travel or
//   March order packs through pulses 1–4 and leaves as a column on pulse 5,
//   unless it starts in hostile ZOC and so can't pack. Anything else stays put.
//   Own columns never block a deployed unit: they stack apart (`positions`).
// * A hex where another own unit's Move ends can be passed but not ended on:
//   two deployed units can't finish in one hex. A hostile hex is the exception:
//   a Move ending there is an attack, and every unit sent at it strikes.
// * Two own units stepping into one hex on the same pulse jam, and neither
//   moves (`traffic_jam`): a step can't take a hex on the pulse another own
//   Move steps into it.
// * An attacker stays in the hex it strikes from unless it wins, and a plan
//   can't count on winning: that hex is held from the pulse the attacker
//   reaches it to the end of the turn (its own hex, for an attack from where it
//   stands), so no other own unit can plan to pass through or end there.
//
// Step `n` of a path is tried on pulse `n` (a blocked step stops the unit, so a
// legal path never waits), which is what lets a later step into a friend's hex
// succeed where an early one wouldn't.

import { axialNeighbors, hexKey } from "@/board/hex";
import type { StepRules } from "@/planning/reach";
import type { components } from "@/lib/api/schema";

import type { WorldOrder } from "./plan";
import { COLUMN_RULES } from "./routing";

type Unit = components["schemas"]["Unit"];
type StandingTravel = components["schemas"]["StandingTravel"];

/** The pulse a deployed unit setting off on a Travel/March order stops holding its hex. */
const PACKED_FROM_PULSE = COLUMN_RULES.packPulses + 1;

/** Hexes holding a unit hostile to `viewer` (every other owner, garrisons included). */
export function hostileHexes(units: readonly Unit[], viewer: string): Set<string> {
  return new Set(units.filter((u) => u.owner !== viewer).map((u) => hexKey(u.coord.q, u.coord.r)));
}

export interface MoveContext {
  /** Every unit the player can see (the fogged world state). */
  units: readonly Unit[];
  viewer: string;
  /** The plan's staged orders by unit id. */
  orders: Readonly<Record<number, WorldOrder>>;
  /** Units carrying on a travel order from an earlier turn. */
  standing: readonly StandingTravel[];
}

/** The step rules for `unit`'s Move this turn (see the module comment). */
export function moveStepRules(unit: Unit, ctx: MoveContext): StepRules {
  const hostile = hostileHexes(ctx.units, ctx.viewer);
  const zoc = new Set<string>();
  for (const u of ctx.units) {
    if (u.owner === ctx.viewer || u.posture !== "deployed") continue;
    for (const n of axialNeighbors(u.coord)) zoc.add(hexKey(n.q, n.r));
  }
  const canAttack = unit.type !== "artillery" && !unit.disordered;
  const standing = new Set(ctx.standing.map((s) => s.unit_id));

  // Own deployed units: the first pulse their hex is free (Infinity: never).
  const freeFrom = new Map<string, number>();
  // Hexes an attacker waits in to strike: the first pulse each is held.
  const heldFrom = new Map<string, number>();
  // Hexes other own Moves step into, and on which pulses (step n on pulse n).
  const entered = new Map<string, Set<number>>();
  const moveEnds = new Set<string>();
  for (const u of ctx.units) {
    if (u.owner !== ctx.viewer || u.id === unit.id || u.posture !== "deployed") continue;
    const key = hexKey(u.coord.q, u.coord.r);
    const order = ctx.orders[u.id];
    if (order?.kind === "move" && order.path.length > 0) {
      const steps = order.path.length;
      order.path.forEach((hex, i) => {
        const k = hexKey(hex.q, hex.r);
        if (!entered.has(k)) entered.set(k, new Set());
        entered.get(k)!.add(i + 1);
      });
      const end = order.path[steps - 1];
      const endKey = hexKey(end.q, end.r);
      moveEnds.add(endKey);
      if (hostile.has(endKey) && steps === 1) {
        freeFrom.set(key, Infinity);
        continue;
      }
      freeFrom.set(key, 1);
      if (hostile.has(endKey)) {
        const from = order.path[steps - 2];
        const fromKey = hexKey(from.q, from.r);
        heldFrom.set(fromKey, steps - 1);
        moveEnds.add(fromKey);
      }
    } else if (order?.kind === "travel" || order?.kind === "march" || (order === undefined && standing.has(u.id))) {
      freeFrom.set(key, zoc.has(key) ? Infinity : PACKED_FROM_PULSE);
    } else {
      freeFrom.set(key, Infinity);
    }
  }

  return {
    canEnter(head, next, step) {
      const key = hexKey(next.q, next.r);
      if (hostile.has(key)) return canAttack;
      if (step < (freeFrom.get(key) ?? 0)) return false;
      if (step >= (heldFrom.get(key) ?? Infinity)) return false;
      if (entered.get(key)?.has(step)) return false;
      return !(zoc.has(hexKey(head.q, head.r)) && zoc.has(key));
    },
    canPass: (hex) => !hostile.has(hexKey(hex.q, hex.r)),
    canEnd: (hex) => {
      const key = hexKey(hex.q, hex.r);
      return hostile.has(key) || !moveEnds.has(key);
    },
  };
}
