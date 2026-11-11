// A world player's plan for one turn (W6 slice 2): one staged order per unit, the
// recruits, a build order and the disband pick, plus what that plan costs.
//
// Pure functions over the fogged world state, so the planning hook and the panels
// share one model and it can be tested without React. The server validates the
// submission (api/app/games/validation.py); these mirror its rules only so the UI
// can offer the right buttons and say why one is missing.

import { axialDistance, hexKey, type Axial } from "@/board/hex";
import { ARTILLERY_RANGE } from "@/planning/rules";
import type { components } from "@/lib/api/schema";

import {
  add,
  covers,
  levelOf,
  NO_STOCKS,
  RESOURCES,
  stocksOf,
  sub,
  type Building,
  type Economy,
  type Stocks,
} from "./economy";
import type { SiteKind, WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = WorldState["units"][number];
type UnitType = components["schemas"]["UnitType"];
type Posture = components["schemas"]["Posture"];
export type Submission = components["schemas"]["SubmitOrdersRequest"];
type WireOrder = NonNullable<Submission["orders"]>[number];

/** A staged order for one unit, before projection to the wire `Order` union. */
export type WorldOrder =
  | { kind: "travel"; destination: Axial; via: Axial[]; onArrival: Posture }
  | { kind: "move"; path: Axial[] }
  | { kind: "march"; path: Axial[]; deployOnArrival: boolean }
  | { kind: "barrage"; target: Axial }
  | { kind: "rest" }
  | { kind: "hold" }
  | { kind: "deploy" };

export interface StagedRecruit {
  coord: Axial;
  type: UnitType;
}

export interface WorldPlan {
  /** Keyed by unit id; absent ⇒ no fresh order (a standing travel order continues). */
  orders: Readonly<Record<number, WorldOrder>>;
  /** Keyed by `hexKey` of the site. At most one recruit per site. */
  recruits: Readonly<Record<string, StagedRecruit>>;
  build: Building | null;
  /** Who goes first if a shortage forces a disband, in order. */
  disband: readonly number[];
}

export const EMPTY_PLAN: WorldPlan = { orders: {}, recruits: {}, build: null, disband: [] };

/** Unit types each site kind recruits (api/app/engine/recruitment.py `WORLD_RECRUIT_SITES`). */
export const RECRUIT_SITES: Record<UnitType, readonly SiteKind[]> = {
  infantry: ["base", "city"],
  artillery: ["base", "city"],
  armor: ["factory"],
};
/** The base building a type needs first (`WORLD_RECRUIT_NEEDS`). */
export const RECRUIT_NEEDS: Partial<Record<UnitType, Building>> = { artillery: "workshop" };

const UNIT_TYPES: readonly UnitType[] = ["infantry", "artillery", "armor"];

// --- wire projection ----------------------------------------------------------------

/** The plan the server holds for this turn, back in staged form (`null`: none yet). */
export function planFromSubmission(submission: Submission | null | undefined): WorldPlan {
  if (submission === null || submission === undefined) return EMPTY_PLAN;
  const orders: Record<number, WorldOrder> = {};
  for (const o of submission.orders ?? []) orders[o.unit_id] = fromWire(o);
  const recruits: Record<string, StagedRecruit> = {};
  for (const r of submission.recruitments ?? []) {
    recruits[hexKey(r.coord.q, r.coord.r)] = { coord: r.coord, type: r.type };
  }
  return { orders, recruits, build: submission.build ?? null, disband: submission.disband ?? [] };
}

function fromWire(o: WireOrder): WorldOrder {
  switch (o.kind) {
    case "travel":
      return { kind: "travel", destination: o.destination, via: o.via ?? [], onArrival: o.on_arrival ?? "deployed" };
    case "move":
      return { kind: "move", path: o.path };
    case "march":
      return { kind: "march", path: o.path, deployOnArrival: o.deploy_on_arrival ?? false };
    case "barrage":
      return { kind: "barrage", target: o.target };
    case "rest":
    case "hold":
    case "deploy":
      return { kind: o.kind };
  }
}

/** The `POST /worlds/{id}/orders` body for this plan. */
export function toSubmission(plan: WorldPlan, slot: string): Submission {
  const orders: WireOrder[] = Object.entries(plan.orders)
    .map(([id, order]) => toWire(Number(id), order))
    .sort((a, b) => a.unit_id - b.unit_id);
  const recruitments = Object.values(plan.recruits).map((r) => ({ owner: slot, type: r.type, coord: r.coord }));
  return { orders, recruitments, build: plan.build, disband: [...plan.disband] };
}

function toWire(unitId: number, o: WorldOrder): WireOrder {
  switch (o.kind) {
    case "travel":
      return { kind: "travel", unit_id: unitId, destination: o.destination, via: o.via, on_arrival: o.onArrival };
    case "move":
      return { kind: "move", unit_id: unitId, path: o.path };
    case "march":
      return { kind: "march", unit_id: unitId, path: o.path, deploy_on_arrival: o.deployOnArrival };
    case "barrage":
      return { kind: "barrage", unit_id: unitId, target: o.target };
    case "rest":
    case "hold":
    case "deploy":
      return { kind: o.kind, unit_id: unitId };
  }
}

/** Whether two plans would submit the same thing (order of map keys aside). */
export function samePlan(a: WorldPlan, b: WorldPlan, slot: string): boolean {
  return normalPlan(a, slot) === normalPlan(b, slot);
}

/** A plan's submission as one canonical string: equal for plans that submit the same thing. */
function normalPlan(p: WorldPlan, slot: string): string {
  const s = toSubmission(p, slot);
  return JSON.stringify({ ...s, recruitments: [...(s.recruitments ?? [])].sort(byCoord) });
}

/** What `submissionKey` gives a turn with nothing submitted. */
export const NO_SUBMISSION = "none";

/**
 * A short stable identity for the plan the server holds this turn (`NO_SUBMISSION`
 * if none): a hash of the canonical submission, so a draft can record which server
 * plan it was forked from and notice when a newer one was sent elsewhere.
 */
export function submissionKey(submission: Submission | null | undefined, slot: string): string {
  if (submission === null || submission === undefined) return NO_SUBMISSION;
  return planKey(planFromSubmission(submission), slot);
}

/** `submissionKey` of the submission `plan` would make. */
export function planKey(plan: WorldPlan, slot: string): string {
  // FNV-1a, 32 bits: plenty to tell one turn's few plans apart.
  let h = 0x811c9dc5;
  const text = normalPlan(plan, slot);
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

function byCoord(a: { coord: Axial }, b: { coord: Axial }): number {
  return a.coord.q - b.coord.q || a.coord.r - b.coord.r;
}

// --- what a unit may be ordered to do -----------------------------------------------

export type OrderKind = WorldOrder["kind"];

/** The order kinds `unit` accepts, as the server validates them. */
export function orderKindsFor(unit: Unit): OrderKind[] {
  if (unit.posture === "column") return ["travel", "march", "deploy", "rest", "hold"];
  const kinds: OrderKind[] = ["travel", "move", "rest", "hold"];
  if (unit.type === "artillery") kinds.push("barrage");
  return kinds;
}

export function inBarrageRange(unit: Unit, target: Axial): boolean {
  return axialDistance(unit.coord, target) <= ARTILLERY_RANGE;
}

// --- base orders --------------------------------------------------------------------

export interface BuildOption {
  building: Building;
  /** The level this order would reach. */
  level: number;
  cost: Stocks;
  turns: number;
  /** Every reason it can't be ordered now, in plain words (empty: it can). */
  blocked: string[];
}

/** What a short budget lacks, in plain words: "needs 5 more steel" (`null`: it covers). */
export function shortfall(have: Stocks, cost: Stocks): string | null {
  const short = RESOURCES.filter((r) => cost[r] > have[r]).map((r) => `${cost[r] - have[r]} more ${r}`);
  return short.length === 0 ? null : `needs ${short.join(" and ")}`;
}

/**
 * The next level of `building` and whether it can be started this turn. `plan`
 * is the rest of the turn's plan: its recruits share the stocks, while a build
 * already queued would be replaced by this one, so its cost doesn't count.
 */
export function buildOption(
  state: WorldState,
  economy: Economy,
  slot: string,
  building: Building,
  plan: WorldPlan = EMPTY_PLAN,
): BuildOption | null {
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) throw new Error(`no player ${slot} in the world view`);
  const work = player.construction ?? null;
  let level = levelOf(player, building);
  if (work !== null && work.building === building && work.completes_on <= state.turn) level = work.level;
  const next = economy.buildings[building][level];
  if (next === undefined) return null;
  const blocked: string[] = [];
  if (work !== null && work.completes_on > state.turn) blocked.push("one building at a time");
  const free = sub(stocksOf(player), recruitsCost(plan, economy));
  const short = shortfall(free, next.cost);
  if (short !== null) blocked.push(short);
  return { building, level: level + 1, cost: next.cost, turns: next.turns, blocked };
}

export interface RecruitOption {
  type: UnitType;
  cost: Stocks;
  /** Every reason it can't be recruited now, in plain words (empty: it can). */
  blocked: string[];
}

/**
 * The unit types `slot` could recruit at `coord`, each with every reason it
 * can't. Empty when the hex isn't a held recruiting site at all. `plan` is the
 * rest of the turn's plan (its build and other recruits share the stocks and the
 * army size; a recruit already staged here would be replaced).
 */
export function recruitOptions(
  state: WorldState,
  map: WorldMap,
  economy: Economy,
  slot: string,
  coord: Axial,
  plan: WorldPlan = EMPTY_PLAN,
  armySize: number = Infinity,
): RecruitOption[] {
  const site = map.siteAt(coord.q, coord.r);
  if (site === undefined) return [];
  const held = state.settlements.some(
    (s) => s.controlled_by === slot && s.coord.q === coord.q && s.coord.r === coord.r,
  );
  if (!held) return [];
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) throw new Error(`no player ${slot} in the world view`);
  const occupied = state.units.some(
    (u) => u.posture === "deployed" && u.coord.q === coord.q && u.coord.r === coord.r,
  );
  const here = hexKey(coord.q, coord.r);
  const others = { ...plan, recruits: Object.fromEntries(Object.entries(plan.recruits).filter(([k]) => k !== here)) };
  const fielded = state.units.filter((u) => u.owner === slot).length + Object.keys(others.recruits).length;
  const free = sub(stocksOf(player), planCost(others, state, economy, slot));
  return UNIT_TYPES.filter((t) => RECRUIT_SITES[t].includes(site.kind)).map((type) => {
    const needs = RECRUIT_NEEDS[type];
    const cost = economy.recruitCost[type];
    const blocked: string[] = [];
    if (needs !== undefined && levelOf(player, needs) < 1) blocked.push(`needs a ${needs}`);
    if (occupied) blocked.push("a unit stands on the hex");
    if (fielded >= armySize) blocked.push(`army is full (${armySize})`);
    const short = shortfall(free, cost);
    if (short !== null) blocked.push(short);
    return { type, cost, blocked };
  });
}

function recruitsCost(plan: WorldPlan, economy: Economy): Stocks {
  return Object.values(plan.recruits).reduce((sum, r) => add(sum, economy.recruitCost[r.type]), NO_STOCKS);
}

/** What the plan spends this turn: the build order plus every recruit. */
export function planCost(plan: WorldPlan, state: WorldState, economy: Economy, slot: string): Stocks {
  let cost = recruitsCost(plan, economy);
  if (plan.build !== null) {
    const option = buildOption(state, economy, slot, plan.build);
    if (option !== null) cost = add(cost, option.cost);
  }
  return cost;
}

/** Problems the server would reject the plan for (budget, army size). */
export function planProblems(
  plan: WorldPlan,
  state: WorldState,
  economy: Economy,
  slot: string,
  armySize: number,
): string[] {
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) throw new Error(`no player ${slot} in the world view`);
  const problems: string[] = [];
  if (!covers(stocksOf(player), planCost(plan, state, economy, slot))) {
    problems.push("The build and recruits cost more than your stocks.");
  }
  const units = state.units.filter((u) => u.owner === slot).length;
  const recruits = Object.keys(plan.recruits).length;
  if (recruits > 0 && units + recruits > armySize) {
    problems.push(`Recruiting takes the army past its size of ${armySize}.`);
  }
  return problems;
}
