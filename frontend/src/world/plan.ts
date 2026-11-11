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

import { add, covers, levelOf, NO_STOCKS, stocksOf, type Building, type Economy, type Stocks } from "./economy";
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
  const norm = (p: WorldPlan) => {
    const s = toSubmission(p, slot);
    return JSON.stringify({ ...s, recruitments: [...(s.recruitments ?? [])].sort(byCoord) });
  };
  return norm(a) === norm(b);
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
  /** Why it can't be ordered now, or `null` if it can. */
  blocked: string | null;
}

/** The next level of `building` and whether it can be started this turn. */
export function buildOption(state: WorldState, economy: Economy, slot: string, building: Building): BuildOption | null {
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) throw new Error(`no player ${slot} in the world view`);
  const work = player.construction ?? null;
  let level = levelOf(player, building);
  if (work !== null && work.building === building && work.completes_on <= state.turn) level = work.level;
  const next = economy.buildings[building][level];
  if (next === undefined) return null;
  let blocked: string | null = null;
  if (work !== null && work.completes_on > state.turn) blocked = "construction in progress";
  else if (!covers(stocksOf(player), next.cost)) blocked = "not enough stocks";
  return { building, level: level + 1, cost: next.cost, turns: next.turns, blocked };
}

export interface RecruitOption {
  type: UnitType;
  cost: Stocks;
  blocked: string | null;
}

/**
 * The unit types `slot` could recruit at `coord`, each with why not if it can't.
 * Empty when the hex isn't a held recruiting site at all.
 */
export function recruitOptions(
  state: WorldState,
  map: WorldMap,
  economy: Economy,
  slot: string,
  coord: Axial,
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
  return UNIT_TYPES.filter((t) => RECRUIT_SITES[t].includes(site.kind)).map((type) => {
    const needs = RECRUIT_NEEDS[type];
    let blocked: string | null = null;
    if (needs !== undefined && levelOf(player, needs) < 1) blocked = `needs a ${needs}`;
    else if (occupied) blocked = "a unit stands here";
    return { type, cost: economy.recruitCost[type], blocked };
  });
}

/** What the plan spends this turn: the build order plus every recruit. */
export function planCost(plan: WorldPlan, state: WorldState, economy: Economy, slot: string): Stocks {
  let cost = NO_STOCKS;
  if (plan.build !== null) {
    const option = buildOption(state, economy, slot, plan.build);
    if (option !== null) cost = add(cost, option.cost);
  }
  for (const r of Object.values(plan.recruits)) cost = add(cost, economy.recruitCost[r.type]);
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
