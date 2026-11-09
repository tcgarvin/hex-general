// What happened last turn, from one player's seat (W6 slice 3).
//
// A world turn resolves twice a day while the player is away, so instead of a
// pulse-by-pulse playback the replay is a report: the player's own news (base,
// fighting, movement) up top, everything else they witnessed folded into "seen",
// and trails for the board (who moved where, where fights and captures were).
// Pure over the fogged replay the API serves (`/worlds/{id}/turns/{turn}`).

import type { Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import type { ReplayEvent } from "@/lib/api/types";
import { eventLines, type NameOf, type Naming } from "@/replay/eventText";

import { NEUTRAL_OWNER } from "./colors";

type UnitType = components["schemas"]["UnitType"];
type Unit = components["schemas"]["Unit"];

export type ReportSection = "base" | "fighting" | "movement" | "seen";
export type ReportTone = "good" | "bad" | "neutral";

export interface ReportItem {
  section: ReportSection;
  tone: ReportTone;
  text: string;
  detail: string[];
  /** Where it happened, for jumping the camera there (`null`: nowhere in particular). */
  at: Axial | null;
}

export interface TurnTrails {
  moves: { unitId: number; owner: string | null; path: Axial[] }[];
  fights: Axial[];
  captures: { coord: Axial; owner: string }[];
  barrages: Axial[];
}

/** Who owns each unit the replay mentions, and its type, from every source it has. */
export interface UnitBook {
  owner(unitId: number): string | null;
  nameOf: NameOf;
  naming: Naming;
}

export function unitBook(events: readonly ReplayEvent[], units: readonly Unit[], viewer: string): UnitBook {
  const known = new Map<number, { owner: string; type: UnitType }>();
  for (const u of units) known.set(u.id, { owner: u.owner, type: u.type });
  for (const e of events) {
    switch (e.kind) {
      case "combat_resolved":
        for (const p of e.participants) known.set(p.unit_id, { owner: p.owner, type: p.unit_type });
        break;
      case "barrage_resolved":
        for (const h of [...(e.hit ? [e.hit] : []), ...(e.extra_hits ?? [])]) {
          known.set(h.unit_id, { owner: h.owner, type: h.unit_type });
        }
        break;
      case "unit_disbanded":
      case "unit_recruited":
      case "world_unit_recruited":
        known.set(e.unit_id, { owner: e.owner, type: e.unit_type });
        break;
      default:
        break;
    }
  }
  const unit = (owner: string, type: UnitType) =>
    owner === viewer ? `Your ${type}` : owner === NEUTRAL_OWNER ? `Garrison ${type}` : `${owner} ${type}`;
  const nameOf: NameOf = (id) => {
    const u = known.get(id);
    return u === undefined ? `Unit ${id}` : unit(u.owner, u.type);
  };
  const player = (id: string) => (id === viewer ? "You" : id === NEUTRAL_OWNER ? "The garrison" : id);
  return { owner: (id) => known.get(id)?.owner ?? null, nameOf, naming: { player, unit } };
}

/** The viewer's report of one resolved turn, grouped and in event order within a group. */
export function turnReport(events: readonly ReplayEvent[], viewer: string, book: UnitBook): ReportItem[] {
  const items: ReportItem[] = [];
  const mine = (id: number) => book.owner(id) === viewer;
  const push = (section: ReportSection, tone: ReportTone, e: ReplayEvent, at: Axial | null) => {
    const [first, ...rest] = eventLines(e, book.nameOf, book.naming);
    items.push({ section, tone, text: first.text, detail: rest.map((l) => l.text), at });
  };
  let repaired = 0;
  for (const e of events) {
    switch (e.kind) {
      case "stocks_resolved":
        if (e.player === viewer) push("base", "neutral", e, null);
        break;
      case "construction_started":
      case "construction_completed":
        push("base", "good", e, null);
        break;
      case "construction_voided":
        push("base", "bad", e, null);
        break;
      case "unit_recruited":
      case "world_unit_recruited":
        if (e.owner === viewer) push("base", "good", e, e.coord);
        break;
      case "recruitment_voided":
        if (e.owner === viewer) push("base", "bad", e, e.coord);
        break;
      case "unit_disbanded":
        if (e.owner === viewer) push("base", "bad", e, e.coord);
        break;
      case "combat_resolved": {
        const ours = e.participants.filter((p) => p.owner === viewer);
        if (ours.length === 0) {
          push("seen", "neutral", e, e.coord);
          break;
        }
        const lost = ours.some((p) => p.destroyed);
        const killed = e.participants.some((p) => p.owner !== viewer && p.destroyed);
        push("fighting", lost ? "bad" : killed ? "good" : "neutral", e, e.coord);
        break;
      }
      case "barrage_resolved": {
        const hits = [...(e.hit ? [e.hit] : []), ...(e.extra_hits ?? [])];
        const firedByUs = mine(e.artillery_id);
        const hitUs = hits.some((h) => h.owner === viewer);
        if (!firedByUs && !hitUs) push("seen", "neutral", e, e.target);
        else push("fighting", hitUs ? "bad" : hits.length > 0 ? "good" : "neutral", e, e.target);
        break;
      }
      case "settlement_captured":
        if (e.captured_by === viewer) push("fighting", "good", e, e.coord);
        else if (e.previous_owner === viewer) push("fighting", "bad", e, e.coord);
        else push("seen", "neutral", e, e.coord);
        break;
      case "travel_ended":
        if (e.owner === viewer) push("movement", e.reason === "destroyed" ? "bad" : "neutral", e, e.coord);
        break;
      case "unit_deployed":
        if (mine(e.unit_id) && (e.cause === "contact" || e.cause === "barraged")) push("movement", "neutral", e, e.dest);
        break;
      case "column_stuck":
      case "column_held":
        if (mine(e.unit_id)) push("movement", "neutral", e, e.coord);
        break;
      case "move_blocked":
        if (mine(e.unit_id)) push("movement", "neutral", e, e.target);
        break;
      case "traffic_jam":
        if (e.unit_ids.some(mine)) push("movement", "neutral", e, e.coord);
        break;
      case "order_mooted":
        if (e.owner === viewer) push("movement", "neutral", e, null);
        break;
      case "unit_repaired":
        if (mine(e.unit_id)) repaired += 1;
        break;
      case "unit_moved":
      case "unit_packed":
      case "economy_resolved":
        break; // movement is drawn as trails; economy_resolved is the 2-player bank
    }
  }
  if (repaired > 0) {
    items.push({
      section: "base",
      tone: "good",
      text: `${repaired} of your units repaired.`,
      detail: [],
      at: null,
    });
  }
  const order: ReportSection[] = ["fighting", "base", "movement", "seen"];
  return order.flatMap((s) => items.filter((i) => i.section === s));
}

/** Movement trails, fights, captures and barrage targets to draw on the board. */
export function turnTrails(events: readonly ReplayEvent[], book: UnitBook): TurnTrails {
  const steps = new Map<number, { pulse: number; origin: Axial; dest: Axial }[]>();
  const fights: Axial[] = [];
  const captures: TurnTrails["captures"] = [];
  const barrages: Axial[] = [];
  for (const e of events) {
    if (e.kind === "unit_moved" || e.kind === "unit_deployed") {
      if (e.origin.q === e.dest.q && e.origin.r === e.dest.r) continue;
      const list = steps.get(e.unit_id) ?? [];
      list.push({ pulse: e.pulse, origin: e.origin, dest: e.dest });
      steps.set(e.unit_id, list);
    } else if (e.kind === "combat_resolved") {
      fights.push(e.coord);
    } else if (e.kind === "settlement_captured") {
      captures.push({ coord: e.coord, owner: e.captured_by });
    } else if (e.kind === "barrage_resolved") {
      barrages.push(e.target);
    }
  }
  const moves = [...steps.entries()].map(([unitId, list]) => {
    list.sort((a, b) => a.pulse - b.pulse);
    return { unitId, owner: book.owner(unitId), path: [list[0].origin, ...list.map((s) => s.dest)] };
  });
  return { moves, fights, captures, barrages };
}
