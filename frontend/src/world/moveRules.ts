// Which hexes a deployed unit's Move can step through or end on this turn, given
// the units the player can see and the rest of the plan. A port of the parts of
// the engine's pulse loop (`api/app/engine/movement.py`) that a planner can
// predict:
//
// * **Hostile units** (any other owner, militia included): stepping
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
//
// Your own units never block planning. These strict rules come first: a hex they
// reach is offered by the path they give. A hex they don't reach, but that
// `provisionalStepRules` (own units left out) does, is offered too, so a Move can
// be staged into a hex a friend has not been ordered out of yet, or into each
// other's hex for a swap, whatever order the player gives them in. The plan then
// warns instead: `planConflicts` re-checks every staged Move against the others'
// orders and says which will be blocked and by whom ("Blocked unless 2nd
// Infantry moves out of the way first"), so the warning clears itself once the
// friend is ordered out. A mutual swap or a closed rotation is not a conflict.

import { axialDistance, axialNeighbors, hexKey, type Axial } from "@/board/hex";
import { reachableContinuations, type HexIndex, type StepRules } from "@/planning/reach";
import type { components } from "@/lib/api/schema";

import { NEUTRAL_OWNER } from "./colors";
import type { WorldOrder } from "./plan";
import { COLUMN_RULES } from "./routing";

type Unit = components["schemas"]["Unit"];
type StandingTravel = components["schemas"]["StandingTravel"];

/** The pulse a deployed unit setting off on a Travel/March order stops holding its hex. */
const PACKED_FROM_PULSE = COLUMN_RULES.packPulses + 1;

/** Hexes holding a unit hostile to `viewer` (every other owner, militia included). */
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

/** Why an own deployed unit is still in its hex on a pulse (see `ownTraffic`). */
export type HoldReason = "stays" | "packs" | "attacks";

/**
 * The first pulse a step into `u`'s hex can land (`Infinity`: never this turn),
 * and why it is held until then. A unit with a Move leaves on pulse 1 (assumed to
 * succeed), unless it attacks from where it stands; one setting off on a Travel or
 * March order (or carrying on a standing one) packs and leaves on pulse 5, unless
 * it starts in hostile ZOC and so can't pack; anything else stays put.
 */
function departure(
  u: Unit,
  order: WorldOrder | undefined,
  travelling: boolean,
  hostile: ReadonlySet<string>,
  zoc: ReadonlySet<string>,
): { pulse: number; reason: HoldReason } {
  if (order?.kind === "move" && order.path.length > 0) {
    const end = order.path[order.path.length - 1];
    if (order.path.length === 1 && hostile.has(hexKey(end.q, end.r))) return { pulse: Infinity, reason: "attacks" };
    return { pulse: 1, reason: "stays" };
  }
  if (order?.kind === "travel" || order?.kind === "march" || (order === undefined && travelling)) {
    return zoc.has(hexKey(u.coord.q, u.coord.r)) ? { pulse: Infinity, reason: "stays" } : { pulse: PACKED_FROM_PULSE, reason: "packs" };
  }
  return { pulse: Infinity, reason: "stays" };
}

/** Hexes next to a visible hostile deployed unit (columns exert no ZOC). */
export function hostileZoc(units: readonly Unit[], viewer: string): Set<string> {
  const zoc = new Set<string>();
  for (const u of units) {
    if (u.owner === viewer || u.posture !== "deployed") continue;
    for (const n of axialNeighbors(u.coord)) zoc.add(hexKey(n.q, n.r));
  }
  return zoc;
}

/** What the other own deployed units do to `unit`'s Move under the plan (see the module comment). */
interface OwnTraffic {
  /** Own deployed units' hexes: the first pulse each is free (Infinity: never), who holds it and why. */
  freeFrom: Map<string, { pulse: number; reason: HoldReason; by: Unit }>;
  /** Hexes an attacker waits in to strike: the first pulse each is held. */
  heldFrom: Map<string, { pulse: number; by: Unit }>;
  /** Hexes other own Moves step into, by pulse (step n on pulse n). */
  entered: Map<string, Map<number, Unit>>;
  /** Where other own Moves end (an attacker's waiting hex too), and the pulse each gets there. */
  moveEnds: Map<string, { pulse: number; by: Unit }>;
}

function ownTraffic(unit: Unit, ctx: MoveContext, hostile: ReadonlySet<string>, zoc: ReadonlySet<string>): OwnTraffic {
  const standing = new Set(ctx.standing.map((s) => s.unit_id));
  const traffic: OwnTraffic = { freeFrom: new Map(), heldFrom: new Map(), entered: new Map(), moveEnds: new Map() };
  for (const u of ctx.units) {
    if (u.owner !== ctx.viewer || u.id === unit.id || u.posture !== "deployed") continue;
    const order = ctx.orders[u.id];
    const leaves = departure(u, order, standing.has(u.id), hostile, zoc);
    traffic.freeFrom.set(hexKey(u.coord.q, u.coord.r), { ...leaves, by: u });
    if (order?.kind !== "move" || order.path.length === 0) continue;
    const steps = order.path.length;
    order.path.forEach((hex, i) => {
      const k = hexKey(hex.q, hex.r);
      if (!traffic.entered.has(k)) traffic.entered.set(k, new Map());
      traffic.entered.get(k)!.set(i + 1, u);
    });
    const end = order.path[steps - 1];
    const endKey = hexKey(end.q, end.r);
    traffic.moveEnds.set(endKey, { pulse: steps, by: u });
    if (hostile.has(endKey) && steps > 1) {
      const from = order.path[steps - 2];
      const fromKey = hexKey(from.q, from.r);
      traffic.heldFrom.set(fromKey, { pulse: steps - 1, by: u });
      traffic.moveEnds.set(fromKey, { pulse: steps - 1, by: u });
    }
  }
  return traffic;
}

/** The step rules for `unit`'s Move this turn (see the module comment). */
export function moveStepRules(unit: Unit, ctx: MoveContext): StepRules {
  const hostile = hostileHexes(ctx.units, ctx.viewer);
  const zoc = hostileZoc(ctx.units, ctx.viewer);
  const canAttack = unit.type !== "artillery" && !unit.disordered;
  const { freeFrom, heldFrom, entered, moveEnds } = ownTraffic(unit, ctx, hostile, zoc);
  return {
    canEnter(head, next, step) {
      const key = hexKey(next.q, next.r);
      if (hostile.has(key)) return canAttack;
      if (step < (freeFrom.get(key)?.pulse ?? 0)) return false;
      if (step >= (heldFrom.get(key)?.pulse ?? Infinity)) return false;
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

/**
 * The step rules for `unit`'s Move with your own units left out: terrain, hostile
 * units, ZOC and the attack rules still apply, but no own unit's hex, end hex,
 * same-pulse step or held attack hex blocks it. A hex reachable only this way is
 * reachable "provisionally": the Move can be staged, and `planConflicts` says
 * what in the plan would still block it.
 */
export function provisionalStepRules(unit: Unit, ctx: MoveContext): StepRules {
  const units = ctx.units.filter((u) => u.owner !== ctx.viewer || u.id === unit.id);
  return moveStepRules(unit, { ...ctx, units, orders: {} });
}

/**
 * Every hex `unit`'s Move can reach this turn, each with its path: from where it
 * stands, and (when `base` is non-empty) by continuing `base`, a continuation
 * winning over a fresh path to the same hex. Under each start, the strict rules'
 * path comes first; a hex only `provisionalStepRules` reach gets that path.
 */
export function planMoveReach(unit: Unit, base: readonly Axial[], index: HexIndex, ctx: MoveContext): Map<string, Axial[]> {
  const strict = moveStepRules(unit, ctx);
  const loose = provisionalStepRules(unit, ctx);
  const from = (start: readonly Axial[]) => {
    const reach = reachableContinuations(unit, start, index, strict);
    for (const [key, path] of reachableContinuations(unit, start, index, loose)) if (!reach.has(key)) reach.set(key, path);
    return reach;
  };
  const reach = from([]);
  if (base.length > 0) for (const [key, path] of from(base)) reach.set(key, path);
  return reach;
}

/** Why a staged Move will be blocked under the plan as it stands, and by which of your units. */
export type MoveConflict =
  /** `by` stays in a hex the path steps into. */
  | { kind: "blocked"; by: Unit }
  /** `by` is still packing for a travel when the path steps into its hex. */
  | { kind: "packing"; by: Unit }
  /** `by` stays in that hex to attack from it (it moves on only if it wins). */
  | { kind: "attacker"; by: Unit }
  /** `by` steps into the same hex on the same pulse: both stop. */
  | { kind: "jam"; by: Unit }
  /** `by` has already stopped in a hex the path passes through. */
  | { kind: "stopped"; by: Unit }
  /** `by` stops first on the hex this Move ends on. */
  | { kind: "sameEnd"; by: Unit };

/**
 * The first step of `path` that the other own units block, under `traffic`, as
 * `{ step, conflict }` (step 1-based), or `null`: the path is clear of them. A
 * step into a friend's hex on the pulse it leaves is fine, so a mutual swap or a
 * closed rotation (each unit's first step into the next one's hex, all on pulse
 * 1) is not a conflict: the engine moves them together (`_friendly_cycles`).
 */
function firstConflict(
  path: readonly Axial[],
  traffic: OwnTraffic,
  hostile: ReadonlySet<string>,
): { step: number; conflict: MoveConflict } | null {
  for (let i = 0; i < path.length; i++) {
    const step = i + 1;
    const key = hexKey(path[i].q, path[i].r);
    if (hostile.has(key)) return null; // an attack: the path ends here
    const free = traffic.freeFrom.get(key);
    if (free !== undefined && step < free.pulse) {
      const kind = free.reason === "packs" ? "packing" : free.reason === "attacks" ? "attacker" : "blocked";
      return { step, conflict: { kind, by: free.by } };
    }
    const held = traffic.heldFrom.get(key);
    if (held !== undefined && step >= held.pulse) return { step, conflict: { kind: "attacker", by: held.by } };
    const jam = traffic.entered.get(key)?.get(step);
    if (jam !== undefined) return { step, conflict: { kind: "jam", by: jam } };
    const ends = traffic.moveEnds.get(key);
    if (ends !== undefined && ends.pulse < step) {
      return { step, conflict: { kind: step === path.length ? "sameEnd" : "stopped", by: ends.by } };
    }
  }
  return null;
}

/**
 * Which staged Moves of your deployed units will be blocked by your own units
 * under the plan as it stands, by unit id. Each path is re-checked step by step
 * against the strict rules built from every other unit's orders, so the answer
 * doesn't depend on which order was given first. A unit that is blocked stops
 * where it is (the engine moves on only with a clear step), so its path is cut
 * there and the others are checked again: a unit following one that is itself
 * blocked is blocked too. Two Moves ending on one hex: the one that gets there
 * second is blocked. Hostile units and ZOC are not checked here (the reach
 * already obeyed them); only your own units' traffic is.
 */
export function planConflicts(ctx: MoveContext): Map<number, MoveConflict> {
  const hostile = hostileHexes(ctx.units, ctx.viewer);
  const zoc = hostileZoc(ctx.units, ctx.viewer);
  const movers: { unit: Unit; path: readonly Axial[] }[] = [];
  for (const u of ctx.units) {
    const order = ctx.orders[u.id];
    if (u.owner !== ctx.viewer || u.posture !== "deployed" || order?.kind !== "move" || order.path.length === 0) continue;
    movers.push({ unit: u, path: order.path });
  }
  const conflicts = new Map<number, MoveConflict>();
  // The plan with each blocked Move cut short where it stops.
  const effective: Record<number, WorldOrder> = { ...ctx.orders };
  // A unit once blocked stays blocked (it only ever gets more company), so this ends.
  for (let added = true; added; ) {
    added = false;
    const found: { unit: Unit; path: readonly Axial[]; step: number; conflict: MoveConflict }[] = [];
    for (const { unit, path } of movers) {
      if (conflicts.has(unit.id)) continue;
      const hit = firstConflict(path, ownTraffic(unit, { ...ctx, orders: effective }, hostile, zoc), hostile);
      if (hit !== null) found.push({ unit, path, ...hit });
    }
    for (const { unit, path, step, conflict } of found) {
      conflicts.set(unit.id, conflict);
      effective[unit.id] = { kind: "move", path: path.slice(0, step - 1) };
      added = true;
    }
  }
  return conflicts;
}

/** The plan warning for a blocked Move, naming the unit in the way with `name`. */
export function conflictText(conflict: MoveConflict, name: (u: Unit) => string): string {
  const other = name(conflict.by);
  switch (conflict.kind) {
    case "blocked":
      return `Blocked unless ${other} moves out of the way first`;
    case "packing":
      return `Blocked: ${other} is still packing up in its hex when this unit gets there`;
    case "attacker":
      return `Blocked: ${other} stays in that hex to attack from it`;
    case "jam":
      return `Jams with ${other}: both step into the same hex at once`;
    case "stopped":
      return `Blocked: ${other} has already stopped in a hex on the way`;
    case "sameEnd":
      return `Ends where ${other} ends: only one can stop there`;
  }
}

export const PINNED_WARNING = "An adjacent enemy that attacks this turn will hit before you move";
export const CANNOT_PACK_WARNING = "It starts next to the enemy, so it can't pack: this Travel ends at once. Move it away first";

/**
 * A one-line warning for an order that steps `unit` away from a visible hostile
 * it starts next to, or `null`.
 *
 * Everyone steps on pulse 1 at once, and a unit whose hex is attacked that pulse
 * is pinned (`_resolve_attacks` in movement.py): it defends, and its own move is
 * cancelled. So a Move away from an adjacent enemy that may attack is a gamble.
 * Militia strike only a unit standing in their site's zone (the site hex and the
 * ring around it, WORLD_SPEC §7.1), a militia gun never moves and the keeper on
 * the site hex always Holds, so an adjacent militia infantry counts only when it is
 * off its post and `unit` stands within 1 of that post. A Travel
 * from next to any hostile deployed unit (militia included) never even packs (§4.4).
 * `hostile` is `hostileHexes`: a Move that ends on one is an attack, not a withdrawal.
 */
export function withdrawalWarning(
  unit: Unit,
  order: WorldOrder | undefined,
  units: readonly Unit[],
  viewer: string,
  hostile: ReadonlySet<string>,
): string | null {
  if (order === undefined || unit.posture !== "deployed") return null;
  const near = new Set(axialNeighbors(unit.coord).map((n) => hexKey(n.q, n.r)));
  const adjacent = units.filter(
    (u) => u.owner !== viewer && u.posture === "deployed" && near.has(hexKey(u.coord.q, u.coord.r)),
  );
  if (adjacent.length === 0) return null;
  if (order.kind === "travel") return CANNOT_PACK_WARNING;
  if (order.kind !== "move") return null;
  const end = order.path[order.path.length - 1];
  if (end !== undefined && hostile.has(hexKey(end.q, end.r))) return null;
  return adjacent.some((u) => mayStrike(u, unit.coord)) ? PINNED_WARNING : null;
}

/** Whether the adjacent hostile `u` may attack a unit standing at `at` this turn. */
function mayStrike(u: Unit, at: Axial): boolean {
  if (u.owner !== NEUTRAL_OWNER) return true;
  if (u.type === "artillery" || u.post === null || u.post === undefined) return false;
  // The keeper (militia infantry on its own site hex) always Holds.
  if (u.coord.q === u.post.q && u.coord.r === u.post.r) return false;
  // A militia picket or a unit going home: only into its own zone.
  return axialDistance(u.post, at) <= 1;
}
