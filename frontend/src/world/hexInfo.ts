// What the hover tooltip says about a hex: terrain, site, owner, the units there.

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { NEUTRAL_OWNER } from "./colors";
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
          ? site.garrison > 0
            ? `Neutral, garrison tier ${site.garrison}`
            : "Neutral"
          : owner === viewer
            ? "Held by you"
            : `Held by ${owner}`,
    );
  }
  if (!visible.has(hexKey(hex.q, hex.r))) {
    lines.push("Not in sight");
    return { title, lines };
  }
  const units = state.units.filter((u) => u.coord.q === hex.q && u.coord.r === hex.r).sort((a, b) => a.id - b.id);
  for (const u of units) {
    const who = u.owner === viewer ? "Your" : u.owner === NEUTRAL_OWNER ? "Garrison" : `${u.owner}'s`;
    const notes = [u.posture === "column" ? "column" : null, u.disordered ? "disordered" : null].filter((n) => n !== null);
    lines.push(`${who} ${u.type}, ${u.hp}/10 HP${notes.length > 0 ? ` (${notes.join(", ")})` : ""}`);
  }
  return { title, lines };
}
