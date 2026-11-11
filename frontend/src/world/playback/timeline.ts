// Turn playback, step 1: the beat timeline (WORLD_SPEC §11, SPEC §11.3).
//
// A resolved world turn arrives as the viewer's fogged event log (`WorldReplay`,
// `GET /worlds/{id}/turns/{turn}`). This module partitions it into ordered
// *beats*, the steps playback walks through:
//
//   barrage (SPEC §11.3 phase 1) → one beat per movement pulse with anything
//   the viewer witnessed (phase 2) → capture → repair → economy → recruit.
//
// Each beat carries renderer-ready *actions* (who, from where to where, combat
// participants and their damage, barrage target and hits, captures) and the
// indices of the events it came from, so the text report can highlight in step.
// Everything here is a pure function of the event list: positions that the log
// does not state (a barrage gun's hex, a swap entrant's destination) are filled
// in by `reconstruct.ts`, which knows where every drawn unit stands.

import type { Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import type { ReplayEvent } from "@/lib/api/types";
import { eventLines } from "@/replay/eventText";

import type { UnitBook } from "../turnReport";

type UnitType = components["schemas"]["UnitType"];
type Posture = components["schemas"]["Posture"];
type Facing = components["schemas"]["Facing"];
type DeployCause = components["schemas"]["DeployCause"];
type MoveBlockedReason = components["schemas"]["MoveBlockedReason"];
type SiteKind = components["schemas"]["SiteKind"];

/** A unit as playback draws it: the `Unit` fields that change during a turn. */
export interface PlaybackUnit {
  id: number;
  owner: string;
  type: UnitType;
  coord: Axial;
  hp: number;
  posture: Posture;
  facing: Facing;
  disordered: boolean;
}

/** One unit's part in a combat (SPEC §12, `CombatParticipant`). */
export interface CombatSide {
  unitId: number;
  owner: string;
  type: UnitType;
  role: "attacker" | "defender";
  damageDealt: number;
  damageTaken: number;
  hpAfter: number;
  destroyed: boolean;
}

/** One unit struck by a barrage (`BarrageHit`). */
export interface BarrageStrike {
  unitId: number;
  owner: string;
  type: UnitType;
  damage: number;
  hpAfter: number;
  destroyed: boolean;
}

/**
 * Something that happens on the board in a beat. Event-derived actions carry
 * `eventIndex` (position in `WorldReplay.events`); the fog actions
 * (`reveal`/`conceal`/`relocate`/`correct`) are added by `reconstruct.ts` where
 * the log alone cannot explain what the viewer sees, and carry none.
 */
export type BeatAction =
  | { kind: "move"; eventIndex: number; unitId: number; from: Axial; to: Axial }
  | { kind: "pack"; eventIndex: number; unitId: number; at: Axial }
  | {
      kind: "deploy";
      eventIndex: number;
      unitId: number;
      from: Axial;
      to: Axial;
      cause: DeployCause;
      disordered: boolean;
    }
  | { kind: "blocked"; eventIndex: number; unitId: number; from: Axial; toward: Axial; reason: MoveBlockedReason }
  | { kind: "jam"; eventIndex: number; unitIds: number[]; at: Axial }
  | { kind: "held"; eventIndex: number; unitId: number; at: Axial; toward: Axial }
  | { kind: "stuck"; eventIndex: number; unitId: number; at: Axial; cause: DeployCause }
  | {
      kind: "combat";
      eventIndex: number;
      at: Axial;
      sides: CombatSide[];
      /** The surviving lead entrant that took the hex, else `null` (nobody entered). */
      entrantId: number | null;
      /**
       * Where the entrant ends up: `at`, except in an edge swap where the event's
       * hex is the entrant's own (then the loser's hex). `null` until
       * `reconstruct.ts` resolves it, and when there is no entrant.
       */
      entrantTo: Axial | null;
    }
  | {
      kind: "barrage";
      eventIndex: number;
      /** The gun that fired; `null` when the server withheld it (a gun the viewer cannot see). */
      gunId: number | null;
      /** The gun's hex; `null` when the viewer never saw the gun (fog). Set by `reconstruct.ts`. */
      from: Axial | null;
      target: Axial;
      strikes: BarrageStrike[];
    }
  | {
      kind: "capture";
      eventIndex: number;
      at: Axial;
      unitId: number;
      owner: string;
      /** `null`: the site was neutral before (`SettlementCaptured.previous_owner`). */
      previousOwner: string | null;
      site: SiteKind | null;
    }
  | { kind: "repair"; eventIndex: number; unitId: number; hpBefore: number; hpAfter: number }
  | {
      kind: "recruit";
      eventIndex: number;
      unitId: number;
      owner: string;
      type: UnitType;
      at: Axial;
      /** The new unit's facing (the engine faces the nearest enemy); set by `reconstruct.ts`. */
      facing: Facing;
    }
  | { kind: "disband"; eventIndex: number; unitId: number; owner: string; type: UnitType; at: Axial }
  /** A unit comes into sight: fades in where it stands. */
  | { kind: "reveal"; unit: PlaybackUnit }
  /** A unit drops out of sight (or died where the viewer could not see): fades out. */
  | { kind: "conceal"; unitId: number }
  /**
   * A drawn unit turns up somewhere the log does not explain (it moved unseen).
   * Playback slides the viewer's own units and fades anyone else out and back in.
   */
  | { kind: "relocate"; unit: PlaybackUnit }
  /**
   * The drawn state disagrees with `state_after` in a way the log does not
   * explain (hp, posture or facing changed unseen): snaps at the end, no animation.
   */
  | { kind: "correct"; unit: PlaybackUnit }
  /** Disorder earned last turn expires at the end of this one (WORLD_SPEC §4.5). */
  | { kind: "steady"; unitId: number };

export type BeatPhase = "barrage" | "pulse" | "capture" | "repair" | "economy" | "recruit" | "settle";

/** End-of-turn phases, in engine order (SPEC §11.3 phases 3-6). */
export const END_PHASES: readonly BeatPhase[] = ["capture", "repair", "economy", "recruit"];

export interface ReportLine {
  eventIndex: number;
  text: string;
}

export interface Beat {
  phase: BeatPhase;
  /** The movement pulse (1-based); `null` exactly when `phase !== "pulse"`. */
  pulse: number | null;
  /** "Barrage", "Pulse 3", "Capture", ... */
  label: string;
  actions: BeatAction[];
  /** Indices into `WorldReplay.events`, in log order: the report lines this beat shows. */
  eventIndices: number[];
  lines: ReportLine[];
}

/** Events that happen inside a movement pulse; every one carries `pulse`. */
type PulseEvent = Extract<
  ReplayEvent,
  {
    kind:
      | "unit_moved"
      | "move_blocked"
      | "traffic_jam"
      | "combat_resolved"
      | "unit_packed"
      | "unit_deployed"
      | "column_stuck"
      | "column_held";
  }
>;

function isPulseEvent(e: ReplayEvent): e is PulseEvent {
  switch (e.kind) {
    case "unit_moved":
    case "move_blocked":
    case "traffic_jam":
    case "combat_resolved":
    case "unit_packed":
    case "unit_deployed":
    case "column_stuck":
    case "column_held":
      return true;
    default:
      return false;
  }
}

/** The beat phase an event without a pulse belongs to; `null` for report-only notes placed later. */
function phaseOf(e: Exclude<ReplayEvent, PulseEvent>): BeatPhase | null {
  switch (e.kind) {
    case "barrage_resolved":
    case "order_mooted":
      return "barrage";
    case "settlement_captured":
      return "capture";
    case "unit_repaired":
      return "repair";
    case "economy_resolved":
    case "stocks_resolved":
    case "unit_disbanded":
    case "construction_started":
    case "construction_completed":
    case "construction_voided":
      return "economy";
    case "unit_recruited":
    case "world_unit_recruited":
    case "recruitment_voided":
      return "recruit";
    case "travel_ended":
      return null;
  }
}

const PHASE_LABEL: Record<BeatPhase, string> = {
  barrage: "Barrage",
  pulse: "Pulse",
  capture: "Capture",
  repair: "Repair",
  economy: "Economy",
  recruit: "Recruitment",
  settle: "End of turn",
};

/** The board action an event becomes; `null` for report-only events (economy, notes). */
export function actionOf(e: ReplayEvent, eventIndex: number): BeatAction | null {
  switch (e.kind) {
    case "unit_moved":
      return { kind: "move", eventIndex, unitId: e.unit_id, from: e.origin, to: e.dest };
    case "unit_packed":
      return { kind: "pack", eventIndex, unitId: e.unit_id, at: e.coord };
    case "unit_deployed":
      return {
        kind: "deploy",
        eventIndex,
        unitId: e.unit_id,
        from: e.origin,
        to: e.dest,
        cause: e.cause,
        disordered: e.disordered,
      };
    case "move_blocked":
      return { kind: "blocked", eventIndex, unitId: e.unit_id, from: e.origin, toward: e.target, reason: e.reason };
    case "traffic_jam":
      return { kind: "jam", eventIndex, unitIds: e.unit_ids, at: e.coord };
    case "column_held":
      return { kind: "held", eventIndex, unitId: e.unit_id, at: e.coord, toward: e.target };
    case "column_stuck":
      return { kind: "stuck", eventIndex, unitId: e.unit_id, at: e.coord, cause: e.cause };
    case "combat_resolved":
      return {
        kind: "combat",
        eventIndex,
        at: e.coord,
        sides: e.participants.map((p) => ({
          unitId: p.unit_id,
          owner: p.owner,
          type: p.unit_type,
          role: p.role,
          damageDealt: p.damage_dealt,
          damageTaken: p.damage_taken,
          hpAfter: p.hp_after,
          destroyed: p.destroyed,
        })),
        entrantId: e.entrant_id ?? null,
        entrantTo: null,
      };
    case "barrage_resolved": {
      const hits = [...(e.hit ? [e.hit] : []), ...(e.extra_hits ?? [])];
      return {
        kind: "barrage",
        eventIndex,
        gunId: e.artillery_id,
        from: null,
        target: e.target,
        strikes: hits.map((h) => ({
          unitId: h.unit_id,
          owner: h.owner,
          type: h.unit_type,
          damage: h.damage,
          hpAfter: h.hp_after,
          destroyed: h.destroyed,
        })),
      };
    }
    case "settlement_captured":
      return {
        kind: "capture",
        eventIndex,
        at: e.coord,
        unitId: e.unit_id,
        owner: e.captured_by,
        previousOwner: e.previous_owner,
        site: e.site ?? null,
      };
    case "unit_repaired":
      return { kind: "repair", eventIndex, unitId: e.unit_id, hpBefore: e.hp_before, hpAfter: e.hp_after };
    case "unit_recruited":
    case "world_unit_recruited":
      return {
        kind: "recruit",
        eventIndex,
        unitId: e.unit_id,
        owner: e.owner,
        type: e.unit_type,
        at: e.coord,
        facing: "right",
      };
    case "unit_disbanded":
      return { kind: "disband", eventIndex, unitId: e.unit_id, owner: e.owner, type: e.unit_type, at: e.coord };
    case "order_mooted":
    case "travel_ended":
    case "economy_resolved":
    case "stocks_resolved":
    case "construction_started":
    case "construction_completed":
    case "construction_voided":
    case "recruitment_voided":
      return null;
  }
}

/** The unit ids an action is about (for fog bookkeeping and the camera). */
export function actionUnitIds(a: BeatAction): number[] {
  switch (a.kind) {
    case "jam":
      return a.unitIds;
    case "combat":
      return a.sides.map((s) => s.unitId);
    case "barrage":
      return [...(a.gunId === null ? [] : [a.gunId]), ...a.strikes.map((s) => s.unitId)];
    case "reveal":
    case "relocate":
    case "correct":
      return [a.unit.id];
    default:
      return [a.unitId];
  }
}

function beatLabel(phase: BeatPhase, pulse: number | null): string {
  return pulse === null ? PHASE_LABEL[phase] : `${PHASE_LABEL[phase]} ${pulse}`;
}

/**
 * Group a turn's witnessed events into ordered beats (see the module header).
 *
 * Within a beat, actions keep the engine's log order (a column packs before it
 * marches; a contact deploy at pulse start precedes that pulse's fights).
 * Report-only notes without a phase (`travel_ended`) are placed in the beat of
 * the unit's last witnessed pulse event, else the last pulse beat, else the
 * first beat. Empty beats are omitted. `book` names units for the report lines.
 */
export function buildTimeline(events: readonly ReplayEvent[], book: UnitBook): Beat[] {
  const pulses = new Map<number, number[]>();
  const phased = new Map<BeatPhase, number[]>();
  const notes: number[] = [];
  const lastPulseOf = new Map<number, number>();
  events.forEach((e, i) => {
    if (isPulseEvent(e)) {
      const list = pulses.get(e.pulse) ?? [];
      list.push(i);
      pulses.set(e.pulse, list);
      const action = actionOf(e, i);
      if (action !== null) for (const id of actionUnitIds(action)) lastPulseOf.set(id, e.pulse);
      return;
    }
    const phase = phaseOf(e);
    if (phase === null) {
      notes.push(i);
      return;
    }
    const list = phased.get(phase) ?? [];
    list.push(i);
    phased.set(phase, list);
  });

  const groups: { phase: BeatPhase; pulse: number | null; indices: number[] }[] = [];
  const barrage = phased.get("barrage");
  if (barrage !== undefined) groups.push({ phase: "barrage", pulse: null, indices: barrage });
  for (const pulse of [...pulses.keys()].sort((a, b) => a - b)) {
    groups.push({ phase: "pulse", pulse, indices: pulses.get(pulse)! });
  }
  for (const phase of END_PHASES) {
    const list = phased.get(phase);
    if (list !== undefined) groups.push({ phase, pulse: null, indices: list });
  }

  for (const i of notes) {
    const e = events[i];
    const unitPulse = e.kind === "travel_ended" ? lastPulseOf.get(e.unit_id) : undefined;
    let target = groups.find((g) => g.phase === "pulse" && g.pulse === unitPulse);
    target ??= groups.filter((g) => g.phase === "pulse").at(-1);
    target ??= groups[0];
    if (target === undefined) {
      groups.push({ phase: "settle", pulse: null, indices: [i] });
    } else {
      target.indices.push(i);
      target.indices.sort((a, b) => a - b);
    }
  }

  return groups.map(({ phase, pulse, indices }) => ({
    phase,
    pulse,
    label: beatLabel(phase, pulse),
    actions: indices.flatMap((i) => {
      const action = actionOf(events[i], i);
      return action === null ? [] : [action];
    }),
    eventIndices: indices,
    lines: indices.map((i) => ({ eventIndex: i, text: eventLines(events[i], book.nameOf, book.naming)[0].text })),
  }));
}

/** The beat that shows event `eventIndex`, for syncing the text report; `-1` if none does. */
export function beatOfEvent(beats: readonly Beat[], eventIndex: number): number {
  return beats.findIndex((b) => b.eventIndices.includes(eventIndex));
}
