// Words the world screen's panels share (desktop shell and phone layout): unit,
// building and site names, how an order reads in a list, and costs in prose.

import type { components } from "@/lib/api/schema";

import { haltText } from "./contact";
import type { Designate } from "./designations";
import { RESOURCES, type Building, type Stocks } from "./economy";
import type { WorldOrder } from "./plan";
import type { Engagement } from "./playback/watchList";
import { SITE_TILE_KINDS } from "./siteTiles";
import type { PlannedRoute } from "./useWorldPlan";
import { at } from "./waypoints";
import type { MapSite } from "./worldMap";

type Unit = components["schemas"]["Unit"];

export const RESOURCE_LABEL = { manpower: "Manpower", fuel: "Fuel", steel: "Steel" } as const;

export const BUILDING_LABEL: Record<Building, string> = {
  barracks: "Barracks",
  depot: "Depot",
  workshop: "Workshop",
};

export const UNIT_LABEL = { infantry: "Infantry", artillery: "Artillery", armor: "Armor" } as const;

export function stocksText(s: Stocks): string {
  const parts = RESOURCES.filter((r) => s[r] !== 0).map((r) => `${s[r]} ${r}`);
  return parts.length === 0 ? "free" : parts.join(", ");
}

/** How a unit's swap reads: who it swaps with and whether it relieves or is relieved. */
export interface SwapInfo {
  partner: Unit;
  role: "relieves" | "relieved" | "swaps";
}

/** A unit's name in prose: its designation ("2nd Infantry"), else type and id. */
export const unitTag = (u: Unit, designate: Designate) => designate(u.id) ?? `${UNIT_LABEL[u.type]} #${u.id}`;

export function orderText(
  order: WorldOrder | undefined,
  route: PlannedRoute | undefined,
  swap: SwapInfo | null = null,
  designate: Designate = () => null,
): string {
  // Kept short: beside a designation ("3rd Infantry") and the HP pips the Army row
  // has room for about a dozen characters. The unit panel says what "no orders" means.
  // A halt on contact leads, so a truncated Army row still shows it.
  if (order === undefined) {
    if (route?.kind !== "standing") return "no orders";
    if (route.halt !== null) return `travelling: ${haltText(route.halt, route.from)}`;
    return route.eta !== null && route.eta <= 1 ? "arriving this turn" : `travelling${turnsText(route.eta)}`;
  }
  switch (order.kind) {
    case "travel": {
      if (route !== undefined && route.halt !== null) return `travel: ${haltText(route.halt, route.from)}`;
      const eta = route?.eta ?? null;
      return eta !== null && eta <= 1 ? "travel, this turn" : `travel${turnsText(eta)}`;
    }
    case "move":
      if (swap !== null) {
        if (swap.role === "relieves") return `relieve ${at(swap.partner.coord)}`;
        // The partner's short name: the Army row has no room for "2nd Infantry".
        const partner = designate(swap.partner.id, true) ?? `#${swap.partner.id}`;
        if (swap.role === "relieved") return `relieved by ${partner}`;
        return `swap with ${partner}`;
      }
      return route?.attack ? `attack ${at(route.attack)}` : `move ${order.path.length} hex${order.path.length === 1 ? "" : "es"}`;
    case "march":
      return "march";
    case "barrage":
      return `barrage ${at(order.target)}`;
    case "rest":
      return "rest";
    case "hold":
      return "hold";
    case "deploy":
      return "deploy";
  }
}

/** ", 3 turns" for a travel taking more than one turn, else nothing. */
export function turnsText(eta: number | null): string {
  return eta !== null && eta > 1 ? `, ${eta} turns` : "";
}

export function etaText(eta: number | null): string {
  if (eta === null) return "";
  if (eta <= 1) return "arrives this turn";
  return `${eta} turns`;
}

export const SECTION_LABEL = { fighting: "Fighting", base: "Base", movement: "Movement", seen: "Also seen" } as const;
// The status greens and reds are tuned for dark wood; on parchment they need more ink.
export const TONE_CLASS = { good: "text-[#466428]", bad: "text-invalid", neutral: "" } as const;

export const FIGHT_RESULT: Record<Engagement["result"], { text: string; tone: keyof typeof TONE_CLASS }> = {
  won: { text: "Won", tone: "good" },
  took: { text: "Took", tone: "good" },
  shelled: { text: "Shelled", tone: "good" },
  held: { text: "Held", tone: "good" },
  lost: { text: "Lost", tone: "bad" },
  lost_site: { text: "Lost", tone: "bad" },
  under_fire: { text: "Shelled", tone: "bad" },
  traded: { text: "Traded", tone: "neutral" },
  repulsed: { text: "Repulsed", tone: "bad" },
  missed: { text: "Missed", tone: "neutral" },
};

export const SITE_LABEL = {
  base: "Base",
  city: "City",
  farm: "Farm",
  oil_field: "Oil field",
  steel_mill: "Steel mill",
  factory: "Factory",
} as const;

/** The militia a neutral site starts with at each tier (WORLD_SPEC §7, §7.1). */
export const GARRISON_TEXT = [
  "",
  "one infantry at 5 HP",
  "one infantry",
  "infantry and a gun",
  "two infantry and a gun",
];

/**
 * A site's panel title: leveled sites (farm, oil field, steel mill, factory) add
 * their level; a base says whose it is, so it never reads like the buildings panel.
 */
export function siteTitle(site: MapSite, owner: string | null | undefined, slot: string): string {
  if (site.kind === "base") return owner === undefined ? "Vacant base site" : owner === slot ? "Your base" : "Enemy base";
  return (SITE_TILE_KINDS as readonly string[]).includes(site.kind)
    ? `${SITE_LABEL[site.kind]} · Level ${site.level}`
    : SITE_LABEL[site.kind];
}

/**
 * A town's status line (a town is held like a site, but earns nothing; its
 * holder sees `TOWN_VISION` hexes around it): "Held by you", "Neutral".
 */
export function townStatus(owner: string | null, slot: string): string {
  return owner === null ? "Neutral" : owner === slot ? "Held by you" : `Held by ${owner}`;
}

/** "5 manpower, 20 fuel, 2 steel": what the stocks hold after the plan's spending. */
export function stocksLeft(s: Stocks): string {
  return RESOURCES.map((r) => `${s[r]} ${r}`).join(", ");
}
