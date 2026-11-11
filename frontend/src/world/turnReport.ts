// What happened last turn, from one player's seat (W6 slice 3).
//
// A world turn resolves twice a day while the player is away, so the replay is
// first a report: the player's own news (base, fighting, movement) up top,
// everything else they witnessed folded into "seen", and trails for the board (who
// moved where, where fights and captures were). The pulse-by-pulse playback on the
// board (`playback/`) highlights these lines in step, through `eventIndex`.
// Pure over the fogged replay the API serves (`/worlds/{id}/turns/{turn}`).
//
// The report's own wording (fights, contact halts, the stock ledger) is written
// here rather than in `replay/eventText.ts`: the playback timeline shares that
// module's one-line-per-event text, while the report folds and summarises.

import { axialDistance, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import type { ReplayEvent } from "@/lib/api/types";
import { eventLines, type NameOf, type Naming } from "@/replay/eventText";

import { NEUTRAL_OWNER } from "./colors";
import { DEPLOY_DISTANCE } from "./contact";
import type { MapSite } from "./worldMap";

type UnitType = components["schemas"]["UnitType"];
type Unit = components["schemas"]["Unit"];
type Settlement = components["schemas"]["Settlement"];
type Stocks = components["schemas"]["Stocks"];
type CombatEvent = Extract<ReplayEvent, { kind: "combat_resolved" }>;
type Participant = CombatEvent["participants"][number];

export type ReportSection = "base" | "fighting" | "movement" | "seen";
export type ReportTone = "good" | "bad" | "neutral";

export interface ReportItem {
  section: ReportSection;
  tone: ReportTone;
  text: string;
  detail: string[];
  /** Where it happened, for jumping the camera there (`null`: nowhere in particular). */
  at: Axial | null;
  /**
   * Index into the replay's `events` of the event this line came from (the first
   * one when it folds several), so playback can highlight the line in step.
   * Absent for summary lines that no single event produced.
   */
  eventIndex?: number;
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

/**
 * What the report may know beyond the events: the static map's sites and the
 * fogged state after the turn. Used to say what a column bumped into when no
 * enemy was in sight.
 */
export interface ReportContext {
  sites: readonly MapSite[];
  settlements: readonly Settlement[];
  /** Units visible after the turn (the replay's `state_after`). */
  units: readonly Unit[];
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

const SITE_NAME: Record<MapSite["kind"], string> = {
  base: "base",
  city: "city",
  farm: "farm",
  oil_field: "oil field",
  steel_mill: "steel mill",
  factory: "factory",
};

const coordText = (a: Axial): string => `(${a.q},${a.r})`;

/** "5 manpower, 2 steel": the non-zero parts of an amount (`nothing` if none). */
export function stocksPhrase(s: Stocks): string {
  const parts = (["manpower", "fuel", "steel"] as const).filter((r) => s[r] !== 0).map((r) => `${s[r]} ${r}`);
  return parts.length === 0 ? "nothing" : parts.join(", ");
}

/** "5 manpower, 0 fuel, 2 steel": every resource, for a stock level. */
function stockLevel(s: Stocks): string {
  return `${s.manpower} manpower, ${s.fuel} fuel, ${s.steel} steel`;
}

const minus = (a: Stocks, b: Stocks): Stocks => ({
  manpower: a.manpower - b.manpower,
  fuel: a.fuel - b.fuel,
  steel: a.steel - b.steel,
});

/** The viewer's report of one resolved turn, grouped and in event order within a group. */
export function turnReport(
  events: readonly ReplayEvent[],
  viewer: string,
  book: UnitBook,
  context: ReportContext | null = null,
): ReportItem[] {
  const items: ReportItem[] = [];
  const mine = (id: number) => book.owner(id) === viewer;
  const push = (section: ReportSection, tone: ReportTone, e: ReplayEvent, at: Axial | null, eventIndex: number) => {
    const [first, ...rest] = eventLines(e, book.nameOf, book.naming);
    items.push({ section, tone, text: first.text, detail: rest.map((l) => l.text), at, eventIndex });
  };
  // A travel that ended on contact folds into the deploy line that explains it.
  const contactEnds = new Map<number, number>();
  // A unit that couldn't pack because contact came first: its blocked step folds
  // into the travel line.
  const packBlocked = new Map<number, number>();
  events.forEach((e, i) => {
    if (e.kind === "travel_ended" && e.owner === viewer && e.reason === "contact") contactEnds.set(e.unit_id, i);
    if (e.kind === "move_blocked" && e.reason === "pack_interrupted") packBlocked.set(e.unit_id, i);
  });
  const folded = new Set<number>();
  const repaired = { count: 0, first: -1 };
  // The stock ledger: what stocks_resolved left, less what the build and recruits
  // then spent (they are paid after it), so the report ends on the stocks the
  // header shows.
  const ledger = { stocks: null as Stocks | null, eventIndex: -1 };
  const spend = (cost: Stocks) => {
    if (ledger.stocks !== null) ledger.stocks = minus(ledger.stocks, cost);
  };

  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    switch (e.kind) {
      case "stocks_resolved": {
        if (e.player !== viewer) break;
        const lost = e.lost_to_storage;
        items.push({
          section: "base",
          tone: "neutral",
          text: `Earned ${stocksPhrase(e.income)}; upkeep ${stocksPhrase(e.upkeep)}.`,
          detail: lost.manpower + lost.fuel + lost.steel > 0 ? [`Lost ${stocksPhrase(lost)}: storage full.`] : [],
          at: null,
          eventIndex: i,
        });
        ledger.stocks = e.after;
        ledger.eventIndex = i;
        break;
      }
      case "construction_started":
        if (e.player !== viewer) break;
        items.push({
          section: "base",
          tone: "good",
          text: `Began ${cap(e.building)} ${e.level} (${stocksPhrase(e.cost)}), done turn ${e.completes_on}.`,
          detail: [],
          at: null,
          eventIndex: i,
        });
        spend(e.cost);
        break;
      case "construction_completed":
        if (e.player === viewer) push("base", "good", e, null, i);
        break;
      case "construction_voided":
        if (e.player === viewer) push("base", "bad", e, null, i);
        break;
      case "unit_recruited":
        if (e.owner === viewer) push("base", "good", e, e.coord, i);
        break;
      case "world_unit_recruited":
        if (e.owner !== viewer) break;
        items.push({
          section: "base",
          tone: "good",
          text: `Recruited ${e.unit_type} at ${coordText(e.coord)} (${stocksPhrase(e.cost)}).`,
          detail: [],
          at: e.coord,
          eventIndex: i,
        });
        spend(e.cost);
        break;
      case "recruitment_voided":
        if (e.owner === viewer) push("base", "bad", e, e.coord, i);
        break;
      case "unit_disbanded":
        if (e.owner === viewer) push("base", "bad", e, e.coord, i);
        break;
      case "combat_resolved": {
        const ours = e.participants.filter((p) => p.owner === viewer);
        const lost = ours.some((p) => p.destroyed);
        const killed = e.participants.some((p) => p.owner !== viewer && p.destroyed);
        items.push({
          ...fightLine(e, viewer, book),
          section: ours.length === 0 ? "seen" : "fighting",
          tone: ours.length === 0 ? "neutral" : lost ? "bad" : killed ? "good" : "neutral",
          at: e.coord,
          eventIndex: i,
        });
        break;
      }
      case "barrage_resolved": {
        const hits = [...(e.hit ? [e.hit] : []), ...(e.extra_hits ?? [])];
        const firedByUs = mine(e.artillery_id);
        const hitUs = hits.some((h) => h.owner === viewer);
        if (!firedByUs && !hitUs) push("seen", "neutral", e, e.target, i);
        else push("fighting", hitUs ? "bad" : hits.length > 0 ? "good" : "neutral", e, e.target, i);
        break;
      }
      case "settlement_captured":
        if (e.captured_by === viewer) push("fighting", "good", e, e.coord, i);
        else if (e.previous_owner === viewer) push("fighting", "bad", e, e.coord, i);
        else push("seen", "neutral", e, e.coord, i);
        break;
      case "travel_ended": {
        if (e.owner !== viewer || folded.has(i)) break;
        if (e.reason !== "contact") {
          push("movement", e.reason === "destroyed" ? "bad" : "neutral", e, e.coord, i);
          break;
        }
        const cause = contactCause(e.coord, viewer, context);
        const stuck = packBlocked.get(e.unit_id);
        items.push({
          section: "movement",
          tone: "neutral",
          text:
            stuck !== undefined
              ? `${book.nameOf(e.unit_id)} could not set off: contact with ${cause}. It stayed deployed at ${coordText(e.coord)}.`
              : `${book.nameOf(e.unit_id)} halted at ${coordText(e.coord)} on contact with ${cause}.`,
          detail: [`It was bound for ${coordText(e.destination)}.`],
          at: e.coord,
          eventIndex: stuck === undefined ? i : Math.min(i, stuck),
        });
        break;
      }
      case "unit_deployed": {
        if (!mine(e.unit_id) || (e.cause !== "contact" && e.cause !== "barraged")) break;
        const ended = contactEnds.get(e.unit_id);
        if (e.cause === "contact") {
          if (ended !== undefined) folded.add(ended);
          items.push({
            section: "movement",
            tone: "neutral",
            ...contactLine(e, ended === undefined ? null : events[ended], book, viewer, context),
            at: e.dest,
            eventIndex: ended === undefined ? i : Math.min(i, ended),
          });
        } else {
          push("movement", "neutral", e, e.dest, i);
        }
        break;
      }
      case "column_stuck":
      case "column_held":
        if (mine(e.unit_id)) push("movement", "neutral", e, e.coord, i);
        break;
      case "move_blocked":
        if (e.reason === "pack_interrupted" && contactEnds.has(e.unit_id)) break; // told with the travel
        if (mine(e.unit_id)) push("movement", "neutral", e, e.target, i);
        break;
      case "traffic_jam":
        if (e.unit_ids.some(mine)) push("movement", "neutral", e, e.coord, i);
        break;
      case "order_mooted":
        if (e.owner === viewer) push("movement", "neutral", e, null, i);
        break;
      case "unit_repaired":
        if (mine(e.unit_id)) {
          if (repaired.count === 0) repaired.first = i;
          repaired.count += 1;
        }
        break;
      case "unit_moved":
      case "unit_packed":
      case "economy_resolved":
        break; // movement is drawn as trails; economy_resolved is the 2-player bank
    }
  }
  if (repaired.count > 0) {
    items.push({
      section: "base",
      tone: "good",
      text: `${repaired.count} of your units repaired.`,
      detail: [],
      at: null,
      eventIndex: repaired.first,
    });
  }
  if (ledger.stocks !== null) {
    items.push({
      section: "base",
      tone: "neutral",
      text: `Stocks now ${stockLevel(ledger.stocks)}.`,
      detail: [],
      at: null,
      eventIndex: ledger.eventIndex,
    });
  }
  const order: ReportSection[] = ["fighting", "base", "movement", "seen"];
  return order.flatMap((s) => items.filter((i) => i.section === s));
}

const cap = (s: string): string => (s.length === 0 ? s : s[0].toUpperCase() + s.slice(1));

/** A unit name for the middle of a sentence ("your armor", "garrison infantry"). */
function mid(name: string): string {
  return name.replace(/^(Your|Garrison) /, (m) => m.toLowerCase());
}

/**
 * One line per fight: who fought whom where, and each side's loss and what is
 * left, e.g. "Attack at (75,4): your armor −1 → 7 HP vs garrison infantry
 * (dug in +3) −1 → 4 HP." Details only for what the line can't carry (who took
 * the hex).
 */
function fightLine(e: CombatEvent, viewer: string, book: UnitBook): { text: string; detail: string[] } {
  const name = (p: Participant) => book.naming.unit(p.owner, p.unit_type);
  const side = (p: Participant) => {
    const mods: string[] = [];
    const dug = p.terrain_bonus + p.hold_bonus;
    if (dug > 0) mods.push(`dug in +${dug}`);
    const penalty = p.defense_penalty ?? 0;
    if (penalty > 0) mods.push(`disordered −${penalty}`);
    const fate = p.destroyed ? "destroyed" : p.damage_taken === 0 ? `unhurt (${p.hp_after} HP)` : `−${p.damage_taken} → ${p.hp_after} HP`;
    return `${mid(name(p))}${mods.length > 0 ? ` (${mods.join(", ")})` : ""} ${fate}`;
  };
  const attackers = e.participants.filter((p) => p.role === "attacker");
  const defenders = e.participants.filter((p) => p.role === "defender");
  const where = coordText(e.coord);
  const ourRole = e.participants.find((p) => p.owner === viewer)?.role ?? null;
  const label = ourRole === "attacker" ? "Attack" : ourRole === "defender" ? "Defence" : "Fight";
  const text =
    defenders.length === 0
      ? `Clash at ${where}: ${attackers.map(side).join(" vs ")}.`
      : `${label} at ${where}: ${attackers.map(side).join(" and ")} vs ${defenders.map(side).join(" and ")}.`;
  const detail: string[] = [];
  if (e.entrant_id !== null && e.entrant_id !== undefined) {
    const entrant = e.participants.find((p) => p.unit_id === e.entrant_id);
    const who = entrant !== undefined ? name(entrant) : book.nameOf(e.entrant_id);
    detail.push(e.entrant_continued ? `${who} took the hex and moved on.` : `${who} took the hex.`);
  }
  return { text, detail };
}

type DeployEvent = Extract<ReplayEvent, { kind: "unit_deployed" }>;

/**
 * A column that halted and deployed on contact, folded with its ended travel.
 * When no enemy is in sight near it, the cause is named from the static map:
 * the nearest neutral garrison it came within reach of.
 */
function contactLine(
  e: DeployEvent,
  ended: ReplayEvent | null,
  book: UnitBook,
  viewer: string,
  context: ReportContext | null,
): { text: string; detail: string[] } {
  const where = coordText(e.dest);
  const spilled = e.dest.q !== e.origin.q || e.dest.r !== e.origin.r;
  const cause = contactCause(e.dest, viewer, context);
  const verb = ended !== null ? "halted and deployed" : "deployed";
  const detail: string[] = [];
  if (spilled) detail.push(`No room at ${coordText(e.origin)}.`);
  if (e.disordered) detail.push("Disordered next turn: weaker defence, can't attack.");
  if (ended !== null && ended.kind === "travel_ended") detail.push(`It was bound for ${coordText(ended.destination)}.`);
  return { text: `${book.nameOf(e.unit_id)} ${verb} at ${where} on contact with ${cause}.`, detail };
}

/** What a column at `at` most likely met: "the enemy", or a named garrisoned site. */
export function contactCause(at: Axial, viewer: string, context: ReportContext | null): string {
  if (context === null) return "the enemy";
  // A column checks contact one step ahead, so the source can be one hex past X.
  const reach = DEPLOY_DISTANCE + 1;
  const enemyNear = context.units.some(
    (u) => u.owner !== viewer && u.owner !== NEUTRAL_OWNER && axialDistance(u.coord, at) <= reach,
  );
  if (enemyNear) return "the enemy";
  const neutral = new Set(
    context.settlements.filter((s) => s.controlled_by === null || s.controlled_by === undefined).map((s) => coordText(s.coord)),
  );
  let best: { site: MapSite; d: number } | null = null;
  for (const site of context.sites) {
    if (site.garrison <= 0 || !neutral.has(coordText(site.coord))) continue;
    const d = axialDistance(site.coord, at);
    if (d <= reach && (best === null || d < best.d)) best = { site, d };
  }
  if (best === null) return "the enemy";
  return `the garrison at the ${SITE_NAME[best.site.kind]} ${coordText(best.site.coord)}`;
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
