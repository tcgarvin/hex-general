// "Which of my units still need orders?" — the core organizing question of the
// planning UI (UI-SPEC §1) — plus the advisory ZOC / traffic predictions of
// SPEC §16.1. Everything here is a pure derivation over a (fog-filtered) set of
// units and the viewer's staged orders; the engine stays the authority, so these
// only flag what *appears* problematic to help a player plan before locking.
//
// ZOC mirrors `api/app/engine/movement.py` exactly: a hex is in enemy ZOC iff it
// is adjacent to ≥1 visible enemy unit; a unit that *begins a pulse* in enemy ZOC
// is engaged and may not step from one ZOC hex to another as a normal move
// (SPEC §8.4) — attacking into an enemy hex or withdrawing to a free hex is fine.
// Traffic mirrors §8.2: friendly units entering the same hex in the same pulse
// jam and stop.

import { axialEqual, axialNeighbors, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { StagedOrder } from "./usePlanning";

type Unit = components["schemas"]["Unit"];

export interface OrderTally {
  /** The viewer's own units. */
  total: number;
  /** How many of them have a staged order. */
  ordered: number;
  /** Ids of own units with no staged order (idle ⇒ they hold position). */
  idleUnitIds: number[];
}

/** Tally staged orders across the viewer's units (`ownUnits` is already slot-filtered). */
export function orderTally(
  ownUnits: readonly Unit[],
  stagedByUnit: Readonly<Record<number, StagedOrder>>,
): OrderTally {
  const idleUnitIds: number[] = [];
  let ordered = 0;
  for (const u of ownUnits) {
    if (stagedByUnit[u.id] !== undefined) ordered += 1;
    else idleUnitIds.push(u.id);
  }
  return { total: ownUnits.length, ordered, idleUnitIds };
}

// --- ZOC / traffic advisories (SPEC §8, surfaced per §16.1) ------------------

/** Severity drives presentation: `error` = the plan will misfire (red, sorted up,
 *  counted in the header); `info` = a heads-up the player may have intended. */
export type WarningSeverity = "error" | "info";

export interface UnitWarning {
  severity: WarningSeverity;
  /** Plain-language text shown on the selected-unit panel (SPEC §16.1). */
  message: string;
}

/** Every hex in a visible enemy unit's Zone of Control — the six hexes around each
 *  enemy (a unit projects ZOC into adjacent hexes, not its own). SPEC §8.3. */
export function enemyZoneOfControl(
  units: readonly Unit[],
  yourSlot: string,
): Set<string> {
  const zoc = new Set<string>();
  for (const u of units) {
    if (u.owner === yourSlot) continue;
    for (const n of axialNeighbors(u.coord)) zoc.add(hexKey(n.q, n.r));
  }
  return zoc;
}

/** Hexes a visible enemy unit stands on (a Move into one is an attack, not a step). */
function enemyOccupied(units: readonly Unit[], yourSlot: string): Set<string> {
  const occupied = new Set<string>();
  for (const u of units) {
    if (u.owner !== yourSlot) occupied.add(hexKey(u.coord.q, u.coord.r));
  }
  return occupied;
}

export interface MovePathAnalysis {
  /** The unit's current hex is in enemy ZOC — it begins the turn engaged (§8.4). */
  startsEngaged: boolean;
  /** First hex the path steps *into* enemy ZOC from outside it (legal, advisory). */
  entersZocAt: Axial | null;
  /** The hex of an illegal ZOC→ZOC step: the unit halts at the *prior* hex and
   *  never reaches this one (§8.4). `null` if the path has no such step. */
  zocStop: Axial | null;
}

/**
 * Walk a move path one step at a time, classifying it against the static enemy
 * ZOC map — exactly the per-pulse check the engine runs, but over fixed enemy
 * positions (hidden or moving enemies may change it, hence "advisory"). Engagement
 * is re-evaluated at each step's *source* hex, so a unit that withdraws out of
 * ZOC and later re-enters is read correctly.
 */
export function analyzeMovePath(
  unit: Unit,
  path: readonly Axial[],
  enemyZoc: ReadonlySet<string>,
  occupied: ReadonlySet<string>,
): MovePathAnalysis {
  const startsEngaged = enemyZoc.has(hexKey(unit.coord.q, unit.coord.r));
  let entersZocAt: Axial | null = null;
  let zocStop: Axial | null = null;

  let source = unit.coord;
  for (const dest of path) {
    const destKey = hexKey(dest.q, dest.r);
    const sourceEngaged = enemyZoc.has(hexKey(source.q, source.r));
    const destInZoc = enemyZoc.has(destKey);

    if (occupied.has(destKey)) {
      // Attack-by-move into an enemy hex: allowed even when engaged; the path
      // resolves into combat here, so nothing past it matters.
      break;
    }
    if (sourceEngaged && destInZoc) {
      // Illegal ZOC→ZOC normal move (§8.4): the unit stays put and stops moving.
      zocStop = dest;
      break;
    }
    if (!sourceEngaged && destInZoc && entersZocAt === null) {
      entersZocAt = dest;
    }
    source = dest;
  }
  return { startsEngaged, entersZocAt, zocStop };
}

/**
 * Friendly traffic jams (§8.2): per movement pulse, any hex two or more of the
 * viewer's own units try to enter at once. A unit halted earlier by an illegal
 * ZOC step never reaches its later hexes, so its path is truncated at `zocStop`
 * before pulses are walked. Returns the jammed hexes and the units involved.
 */
export function trafficJams(
  stagedByUnit: Readonly<Record<number, StagedOrder>>,
  unitIds: ReadonlySet<number>,
  zocStopByUnit: ReadonlyMap<number, Axial>,
): { hexes: Set<string>; unitIds: Set<number> } {
  const paths: { id: number; path: readonly Axial[] }[] = [];
  for (const [idStr, order] of Object.entries(stagedByUnit)) {
    if (order.kind !== "move") continue;
    const id = Number(idStr);
    if (!unitIds.has(id)) continue;
    let path: readonly Axial[] = order.path;
    const stop = zocStopByUnit.get(id);
    if (stop !== undefined) {
      const i = path.findIndex((p) => axialEqual(p, stop));
      if (i >= 0) path = path.slice(0, i); // halt *before* the illegal hex
    }
    if (path.length > 0) paths.push({ id, path });
  }

  const hexes = new Set<string>();
  const jammed = new Set<number>();
  const maxLen = paths.reduce((m, p) => Math.max(m, p.path.length), 0);
  for (let pulse = 0; pulse < maxLen; pulse++) {
    const entrants = new Map<string, number[]>();
    for (const { id, path } of paths) {
      if (pulse >= path.length) continue;
      const step = path[pulse];
      const key = hexKey(step.q, step.r);
      const list = entrants.get(key);
      if (list === undefined) entrants.set(key, [id]);
      else list.push(id);
    }
    for (const [key, ids] of entrants) {
      if (ids.length >= 2) {
        hexes.add(key);
        for (const id of ids) jammed.add(id);
      }
    }
  }
  return { hexes, unitIds: jammed };
}

export interface PlanAdvisory {
  /** Enemy ZOC hexes, for the board halo (SPEC §16.1 "see the current enemy ZOC"). */
  enemyZoc: ReadonlySet<string>;
  /** Hexes where friendly units jam in the same pulse, for board markers. */
  trafficHexes: ReadonlySet<string>;
  /** Per unit, the hex its move is blocked at by an illegal ZOC step (board ✕). */
  zocStopByUnit: ReadonlyMap<number, Axial>;
  /** Warnings per unit id, for the roster + selected-unit panel. */
  byUnit: ReadonlyMap<number, UnitWarning[]>;
  /** Ids with an `error`-severity problem — roster emphasis + sort. */
  problemUnitIds: ReadonlySet<number>;
  /** Count of units with a problem, for the header badge. */
  count: number;
}

/** Derive the full advisory picture for the viewer's staged plan (SPEC §16.1). */
export function planAdvisory(
  units: readonly Unit[],
  yourSlot: string,
  stagedByUnit: Readonly<Record<number, StagedOrder>>,
): PlanAdvisory {
  const enemyZoc = enemyZoneOfControl(units, yourSlot);
  const occupied = enemyOccupied(units, yourSlot);

  const byUnit = new Map<number, UnitWarning[]>();
  const zocStopByUnit = new Map<number, Axial>();
  const problemUnitIds = new Set<number>();
  const ownIds = new Set<number>();

  const add = (id: number, warning: UnitWarning) => {
    const list = byUnit.get(id);
    if (list === undefined) byUnit.set(id, [warning]);
    else list.push(warning);
  };

  for (const u of units) {
    if (u.owner !== yourSlot) continue;
    ownIds.add(u.id);
    const order = stagedByUnit[u.id];
    const path = order?.kind === "move" ? order.path : [];
    const a = analyzeMovePath(u, path, enemyZoc, occupied);

    if (a.zocStop !== null) {
      zocStopByUnit.set(u.id, a.zocStop);
      problemUnitIds.add(u.id);
      add(u.id, {
        severity: "error",
        message: `Illegal move along the enemy line — halts before (${a.zocStop.q},${a.zocStop.r}).`,
      });
    } else if (a.entersZocAt !== null) {
      add(u.id, {
        severity: "info",
        message: `Path enters enemy ZOC at (${a.entersZocAt.q},${a.entersZocAt.r}).`,
      });
    }
    if (a.startsEngaged) {
      add(u.id, {
        severity: "info",
        message: "In enemy ZOC — may attack, hold, or withdraw, not move along the line.",
      });
    }
  }

  const traffic = trafficJams(stagedByUnit, ownIds, zocStopByUnit);
  for (const id of traffic.unitIds) {
    problemUnitIds.add(id);
    add(id, { severity: "error", message: "Traffic conflict with another of your units." });
  }

  return {
    enemyZoc,
    trafficHexes: traffic.hexes,
    zocStopByUnit,
    byUnit,
    problemUnitIds,
    count: problemUnitIds.size,
  };
}

/** Sort a unit's warnings most-severe first (errors before info). */
export function sortWarnings(warnings: readonly UnitWarning[]): UnitWarning[] {
  return [...warnings].sort((a, b) =>
    a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1,
  );
}
