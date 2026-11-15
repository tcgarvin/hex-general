// Militia (WORLD_SPEC §7.1): the neutral units of a site. Each belongs to one site
// for life (`Unit.post`, the site's hex) and stays in its zone, the site hex and
// the ring of six around it. Fog treats them like any other unit, and the view
// carries no militia orders, so nothing here predicts what they will do; these
// helpers only name and measure the militia the viewer can see.

import type { Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import { UNIT_STATS } from "@/planning/rules";

import { NEUTRAL_OWNER } from "./colors";
import { GARRISON_FORCES } from "./manualRules";
import type { WorldMap } from "./worldMap";

type UnitType = components["schemas"]["UnitType"];

/** The unit fields the helpers read (a `Unit` or a playback unit). */
export interface MilitiaLike {
  owner: string;
  type: UnitType;
  /** The site hex of a militia unit; `null`/absent for a player's unit (or a playback unit whose post is unknown). */
  post?: Axial | null;
}

/** Whether `u` is a militia unit. */
export const isMilitia = (u: { owner: string }): boolean => u.owner === NEUTRAL_OWNER;

/**
 * The HP a unit starts with, which its HP bar is measured against: a player's
 * unit its type's full HP, a militia unit the HP its site's tier gives that type
 * (`GARRISON_FORCES`: 5 for a tier-1 infantry). A militia unit whose post is
 * unknown (playback names some units only by an event) counts at full HP.
 */
export function startingHp(u: MilitiaLike, map: WorldMap): number {
  const full = UNIT_STATS[u.type].maxHp;
  if (!isMilitia(u) || u.post === null || u.post === undefined) return full;
  const site = map.siteAt(u.post.q, u.post.r);
  if (site === undefined) throw new Error(`militia post (${u.post.q}, ${u.post.r}) is not a site`);
  const force = GARRISON_FORCES[site.garrison]?.find(([type]) => type === u.type);
  if (force === undefined) throw new Error(`a tier-${site.garrison} militia has no ${u.type}`);
  return force[1];
}

/** "Militia infantry". */
export const militiaName = (type: UnitType): string => `Militia ${type}`;
