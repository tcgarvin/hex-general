// The world economy as the client forecasts it (mirrors api/app/engine/world_economy.py
// and GameState.unit_cap_for). Pure functions over the fogged world state, for the
// resource bar, the base panel and shortage warnings. The server is the authority;
// this only tells the player what next turn will look like if nothing changes.

import type { components } from "@/lib/api/schema";

import type { MapSite, SiteKind, WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Player = components["schemas"]["Player"];
export type Stocks = components["schemas"]["Stocks"];
export type Building = components["schemas"]["Building"];
type UnitType = components["schemas"]["UnitType"];

export interface Economy {
  siteIncome: Record<SiteKind, Stocks>;
  /** Extra income per site level above 1 (cities and bases are always level 1). */
  levelIncome: Record<SiteKind, Stocks>;
  recruitCost: Record<UnitType, Stocks>;
  upkeep: Record<UnitType, Stocks>;
  buildings: Record<Building, { cost: Stocks; turns: number }[]>;
  storage: number[];
  army: { base: number; per_barracks: number; sites_per_point: number; ceiling: number };
}

export const NO_STOCKS: Stocks = { manpower: 0, fuel: 0, steel: 0 };
export const RESOURCES = ["manpower", "fuel", "steel"] as const;
export type Resource = (typeof RESOURCES)[number];

/**
 * The world's economy tables from its config. Throws if the config has none: a
 * world view always carries them, so a missing table is a contract break.
 */
export function economyOf(state: WorldState): Economy {
  const e = state.config.economy;
  if (
    e === undefined ||
    e === null ||
    e.site_income === undefined ||
    e.level_income === undefined ||
    e.recruit_cost === undefined ||
    e.upkeep === undefined ||
    e.buildings === undefined ||
    e.storage === undefined ||
    e.army === undefined
  ) {
    throw new Error("world state has no economy config");
  }
  const army = e.army;
  return {
    siteIncome: e.site_income as Record<SiteKind, Stocks>,
    levelIncome: e.level_income as Record<SiteKind, Stocks>,
    recruitCost: e.recruit_cost as Record<UnitType, Stocks>,
    upkeep: e.upkeep as Record<UnitType, Stocks>,
    buildings: e.buildings as Economy["buildings"],
    storage: e.storage,
    army: {
      base: army.base ?? 4,
      per_barracks: army.per_barracks ?? 2,
      sites_per_point: army.sites_per_point ?? 4,
      ceiling: army.ceiling ?? 15,
    },
  };
}

export function add(a: Stocks, b: Stocks): Stocks {
  return { manpower: a.manpower + b.manpower, fuel: a.fuel + b.fuel, steel: a.steel + b.steel };
}

export function sub(a: Stocks, b: Stocks): Stocks {
  return { manpower: a.manpower - b.manpower, fuel: a.fuel - b.fuel, steel: a.steel - b.steel };
}

/** What a held site earns a turn at its level (mirrors `WorldEconomy.income`). */
export function siteIncome(economy: Economy, site: MapSite): Stocks {
  const extra = economy.levelIncome[site.kind];
  const n = site.level - 1;
  return add(economy.siteIncome[site.kind], {
    manpower: extra.manpower * n,
    fuel: extra.fuel * n,
    steel: extra.steel * n,
  });
}

export function covers(have: Stocks, cost: Stocks): boolean {
  return have.manpower >= cost.manpower && have.fuel >= cost.fuel && have.steel >= cost.steel;
}

export function levelOf(player: Player, building: Building): number {
  return player.buildings?.[building] ?? 0;
}

/**
 * The level of `building` this turn's economy and recruits meet: a construction
 * due by `turn` counts, because the economy phase finishes it before settling
 * stocks and before recruitment (`Player.level_on` in api/app/engine/models.py).
 */
export function levelThisTurn(player: Player, building: Building, turn: number): number {
  const work = player.construction ?? null;
  if (work !== null && work.building === building && work.completes_on <= turn) return work.level;
  return levelOf(player, building);
}

/** The army size for a barracks level and a count of held sites (`ArmySize.size`). */
export function armySizeFor(economy: Economy, barracks: number, heldSites: number): number {
  const a = economy.army;
  return Math.min(a.ceiling, a.base + a.per_barracks * barracks + Math.floor(heldSites / a.sites_per_point));
}

export function stocksOf(player: Player): Stocks {
  return player.stocks ?? NO_STOCKS;
}

export interface Forecast {
  income: Stocks;
  upkeep: Stocks;
  net: Stocks;
  /** Next turn's stocks if nothing is spent (floored at 0, limited to storage). */
  next: Stocks;
  storage: number;
  /** Resources that will run out next turn, forcing a disband. */
  short: Resource[];
  units: number;
  armySize: number;
  heldSites: number;
}

/** What `slot`'s economy does next turn if they order nothing (see module note). */
export function forecast(state: WorldState, map: WorldMap, slot: string): Forecast {
  const economy = economyOf(state);
  const player = state.players.find((p) => p.id === slot);
  if (player === undefined) throw new Error(`no player ${slot} in the world view`);
  let income = NO_STOCKS;
  let heldSites = 0;
  for (const s of state.settlements) {
    if (s.controlled_by !== slot) continue;
    const site = map.siteAt(s.coord.q, s.coord.r);
    if (site === undefined) throw new Error(`settlement at ${s.coord.q},${s.coord.r} has no site`);
    income = add(income, siteIncome(economy, site));
    if (site.kind !== "base") heldSites += 1;
  }
  let upkeep = NO_STOCKS;
  let units = 0;
  for (const u of state.units) {
    if (u.owner !== slot) continue;
    upkeep = add(upkeep, economy.upkeep[u.type]);
    units += 1;
  }
  const net = sub(income, upkeep);
  const raw = add(stocksOf(player), net);
  const storage = economy.storage[levelThisTurn(player, "depot", state.turn)];
  const clamp = (n: number) => Math.max(0, Math.min(storage, n));
  const short = (["fuel", "manpower"] as const).filter((r) => raw[r] < 0);
  const armySize = armySizeFor(economy, levelThisTurn(player, "barracks", state.turn), heldSites);
  return {
    income,
    upkeep,
    net,
    next: { manpower: clamp(raw.manpower), fuel: clamp(raw.fuel), steel: clamp(raw.steel) },
    storage,
    short,
    units,
    armySize,
    heldSites,
  };
}
