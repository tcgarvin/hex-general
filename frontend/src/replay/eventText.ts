// Plain-language rendering of resolved events for the replay log (UI-SPEC §13.3,
// SPEC §16.3). Pure and framework-free so it can be unit-tested and shared by the
// Pixi log panel. Each event becomes one or more indented lines; `unit_moved` is
// handled by the log model (collapsed into a summary), not here.

import type { components } from "@/lib/api/schema";
import type { ReplayEvent } from "@/lib/api/types";

type Axial = components["schemas"]["Axial"];
type UnitType = components["schemas"]["UnitType"];

/** One rendered line of the event log. `indent` 1 is a continuation/detail line. */
export interface LogLine {
  text: string;
  indent: 0 | 1;
}

/** Look up a unit's display name by id; used for events that carry only an id. */
export type NameOf = (unitId: number) => string;

const cap = (s: string): string => (s.length === 0 ? s : s[0].toUpperCase() + s.slice(1));

/** "Blue Armor" from owner slot + unit type. */
export function unitName(owner: string, type: UnitType): string {
  return `${cap(owner)} ${cap(type)}`;
}

const coord = (a: Axial): string => `(${a.q},${a.r})`;

const MOVE_BLOCKED_REASON: Record<
  Exclude<components["schemas"]["MoveBlockedReason"], "zoc_locked">,
  string
> = {
  off_map: "the move would leave the map",
  impassable: "the terrain is impassable",
  occupied: "the hex was already occupied",
  attack_forbidden: "it may not attack from there",
};

const RECRUIT_VOIDED_REASON: Record<
  components["schemas"]["RecruitmentVoidedReason"],
  string
> = {
  city_not_friendly: "the city was no longer friendly",
  hex_occupied: "the hex was occupied",
  unit_cap_reached: "the unit cap was reached",
  insufficient_muster: "the bank fell short",
};

/**
 * Render a single resolved event as one or more log lines (SPEC §16.3). `nameOf`
 * resolves the display name of units the event references only by id.
 */
export function eventLines(event: ReplayEvent, nameOf: NameOf): LogLine[] {
  switch (event.kind) {
    case "unit_moved":
      // Collapsed into a per-beat summary by the log model — never rendered here.
      return [{ indent: 0, text: `${nameOf(event.unit_id)} moved to ${coord(event.dest)}.` }];

    case "barrage_resolved": {
      const firer = nameOf(event.artillery_id);
      const hit = event.hit;
      if (hit === null || hit === undefined) {
        return [{ indent: 0, text: `${firer} barraged ${coord(event.target)} — no unit there.` }];
      }
      const target = unitName(hit.owner, hit.unit_type);
      const lines: LogLine[] = [
        { indent: 0, text: `${firer} barraged ${target} at ${coord(event.target)}.` },
      ];
      const bonus = defenseBonusText(hit.terrain_bonus, hit.hold_bonus);
      if (bonus !== null) lines.push({ indent: 1, text: `${target} ${bonus}.` });
      lines.push({ indent: 1, text: `Dealt ${hit.damage} damage.` });
      lines.push({
        indent: 1,
        text: hit.destroyed ? `${target} was destroyed.` : `${target} is at ${hit.hp_after} HP.`,
      });
      return lines;
    }

    case "combat_resolved": {
      const attackers = event.participants.filter((p) => p.role === "attacker");
      const defender = event.participants.find((p) => p.role === "defender");
      const nameOfP = (p: (typeof event.participants)[number]) => unitName(p.owner, p.unit_type);

      const attackerLabel =
        attackers.length === 0
          ? "Units"
          : attackers.map(nameOfP).join(" and ");

      const lines: LogLine[] = [];
      if (defender !== undefined) {
        lines.push({
          indent: 0,
          text: `${attackerLabel} attacked ${nameOfP(defender)} at ${coord(event.coord)}.`,
        });
        const bonus = defenseBonusText(defender.terrain_bonus, defender.hold_bonus);
        if (bonus !== null) lines.push({ indent: 1, text: `${nameOfP(defender)} ${bonus}.` });
      } else {
        lines.push({ indent: 0, text: `${attackerLabel} clashed at ${coord(event.coord)}.` });
      }

      for (const p of event.participants) {
        if (p.damage_dealt > 0) {
          lines.push({ indent: 1, text: `${nameOfP(p)} dealt ${p.damage_dealt} damage.` });
        }
      }
      for (const p of event.participants) {
        if (p.destroyed) lines.push({ indent: 1, text: `${nameOfP(p)} was destroyed.` });
      }

      if (event.entrant_id !== null && event.entrant_id !== undefined) {
        const entrant = event.participants.find((p) => p.unit_id === event.entrant_id);
        const name = entrant !== undefined ? nameOfP(entrant) : nameOf(event.entrant_id);
        lines.push({
          indent: 1,
          text: event.entrant_continued
            ? `${name} took the hex and continued moving.`
            : `${name} took the hex.`,
        });
      }
      return lines;
    }

    case "settlement_captured": {
      const who = nameOf(event.unit_id);
      const from =
        event.previous_owner === null
          ? " (was neutral)"
          : ` from ${cap(event.previous_owner)}`;
      return [
        {
          indent: 0,
          text: `${who} captured the ${event.settlement} at ${coord(event.coord)}${from}.`,
        },
      ];
    }

    case "unit_repaired": {
      const where = event.in_settlement ? " in a settlement" : "";
      return [
        {
          indent: 0,
          text: `${nameOf(event.unit_id)} repaired ${event.hp_before}→${event.hp_after} HP${where}.`,
        },
      ];
    }

    case "economy_resolved": {
      const net = event.net >= 0 ? `+${event.net}` : `${event.net}`;
      const lines: LogLine[] = [
        {
          indent: 0,
          text: `${cap(event.player)}: +${event.income} income, −${event.upkeep} upkeep (net ${net}). Bank ${event.bank_before}→${event.bank_after}.`,
        },
      ];
      if (event.lost_to_cap > 0) {
        lines.push({ indent: 1, text: `${event.lost_to_cap} muster lost to the bank cap.` });
      }
      return lines;
    }

    case "unit_recruited":
      return [
        {
          indent: 0,
          text: `${cap(event.owner)} recruited ${cap(event.unit_type)} at ${coord(event.coord)} (−${event.cost}, bank ${event.bank_after}).`,
        },
      ];

    case "move_blocked": {
      const who = nameOf(event.unit_id);
      if (event.reason === "zoc_locked") {
        return [
          { indent: 0, text: `${who} began the pulse in enemy ZOC.` },
          { indent: 1, text: `Its move to ${coord(event.target)} would enter another enemy-controlled hex.` },
          { indent: 1, text: `That move is illegal — ${who} held position.` },
        ];
      }
      return [
        {
          indent: 0,
          text: `${who}'s move to ${coord(event.target)} was blocked — ${MOVE_BLOCKED_REASON[event.reason]}.`,
        },
      ];
    }

    case "traffic_jam":
      return [
        {
          indent: 0,
          text: `Traffic jam at ${coord(event.coord)}: ${event.unit_ids.length} friendly units collided and stopped.`,
        },
      ];

    case "order_mooted":
      // The ordered unit died in barrage before its order could apply (SPEC §11.3).
      return [
        { indent: 0, text: `${nameOf(event.unit_id)}'s order was mooted — destroyed by barrage.` },
      ];

    case "recruitment_voided":
      return [
        {
          indent: 0,
          text: `${unitName(event.owner, event.unit_type)} recruit at ${coord(event.coord)} was voided — ${RECRUIT_VOIDED_REASON[event.reason]}.`,
        },
      ];
  }
}

/** "+2 terrain +1 hold defense" style suffix, or null when there is no bonus. */
function defenseBonusText(terrainBonus: number, holdBonus: number): string | null {
  const parts: string[] = [];
  if (terrainBonus > 0) parts.push(`+${terrainBonus} terrain`);
  if (holdBonus > 0) parts.push(`+${holdBonus} hold`);
  if (parts.length === 0) return null;
  return `had ${parts.join(" ")} defense`;
}
