// What the hover tooltip says about a hex: terrain, site, owner, the units there.

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import type { Designate } from "./designations";
import { GARRISON_TEXT, townStatus } from "./labels";
import { isMilitia, militiaName, startingHp } from "./militia";
import type { WorldMap } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];

const TERRAIN_LABEL: Record<string, string> = {
  plains: "Plains",
  woods: "Woods",
  hills: "Hills",
  river: "River",
  bridge: "Bridge",
  lake: "Lake",
  town: "Town",
  city: "City",
};

const SITE_LABEL: Record<string, string> = {
  base: "Base",
  city: "City",
  farm: "Farm",
  oil_field: "Oil field",
  steel_mill: "Steel mill",
  factory: "Factory",
};

const LEVELED = new Set(["farm", "oil_field", "steel_mill", "factory"]);

/** "of the farm (3,4)" for a militia unit standing off its site hex (on its ring); else `null`. */
function postNote(map: WorldMap, post: Axial | null, hex: Axial): string | null {
  if (post === null || (post.q === hex.q && post.r === hex.r)) return null;
  const site = map.siteAt(post.q, post.r);
  if (site === undefined) throw new Error(`militia post (${post.q}, ${post.r}) is not a site`);
  return `of the ${SITE_LABEL[site.kind].toLowerCase()} (${post.q},${post.r})`;
}

/** A neutral site's status clause, shared with the hex and site panels: "Neutral; militia: one infantry". */
export const neutralStatus = (garrison: number): string =>
  garrison > 0 ? `Neutral; militia: ${GARRISON_TEXT[garrison]}` : "Neutral";

export interface HexInfo {
  title: string;
  lines: string[];
}

/** The tooltip for `hex` as `viewer` sees it (units only where in sight). */
export function hexInfo(
  map: WorldMap,
  state: WorldState,
  visible: ReadonlySet<string>,
  viewer: string,
  hex: Axial,
  designate: Designate = () => null,
): HexInfo {
  const cell = map.hexAt(hex.q, hex.r);
  if (cell === undefined) throw new Error(`hex (${hex.q}, ${hex.r}) is off the map`);
  const site = map.siteAt(hex.q, hex.r);
  const terrain = `${TERRAIN_LABEL[cell.terrain]}${cell.road ? ", road" : ""}`;
  const lines: string[] = [];
  let title = terrain;
  if (site !== undefined) {
    title = LEVELED.has(site.kind) ? `${SITE_LABEL[site.kind]}, level ${site.level}` : SITE_LABEL[site.kind];
    if (site.kind === "city") lines.push(terrain);
    const settlement = state.settlements.find((s) => s.coord.q === hex.q && s.coord.r === hex.r);
    const owner = settlement?.controlled_by ?? null;
    lines.push(
      settlement === undefined
        ? "Vacant base site"
        : owner === null
          ? neutralStatus(site.garrison)
          : owner === viewer
            ? "Held by you"
            : `Held by ${owner}`,
    );
  } else if (cell.terrain === "town") {
    // A town is held like a site: every world town has a settlement since 2026-11-15.
    const settlement = state.settlements.find((s) => s.coord.q === hex.q && s.coord.r === hex.r);
    if (settlement !== undefined) lines.push(townStatus(settlement.controlled_by ?? null, viewer));
  }
  if (!visible.has(hexKey(hex.q, hex.r))) {
    lines.push("Not in sight");
    return { title, lines };
  }
  const units = state.units.filter((u) => u.coord.q === hex.q && u.coord.r === hex.r).sort((a, b) => a.id - b.id);
  for (const u of units) {
    const name =
      u.owner === viewer
        ? (designate(u.id) ?? `Your ${u.type}`)
        : isMilitia(u)
          ? militiaName(u.type)
          : `${u.owner}'s ${u.type}`;
    const notes = [
      u.posture === "column" ? "column" : null,
      u.disordered ? "disordered" : null,
      postNote(map, u.post ?? null, hex),
    ].filter((n) => n !== null);
    lines.push(`${name}, ${u.hp}/${startingHp(u, map)} HP${notes.length > 0 ? ` (${notes.join(", ")})` : ""}`);
  }
  return { title, lines };
}
