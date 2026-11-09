// Which tile texture a hex is drawn with (shared by the 2-player board and the
// world board), so a hex looks the same on both.

import type { components } from "@/lib/api/schema";

import { hexKey } from "./hex";
import type { PortraitTile } from "./panels/SelectedUnitPanel";
import { orientedRiverTile } from "./riverTiles";
import type { RoadTileRef } from "./roadTiles";
import { variantIndex, type LoadedTiles } from "./tiles";

type Hex = components["schemas"]["Hex"];

/**
 * The texture + mirror the board draws for a hex, in branch priority order:
 * river/bridge oriented art (chosen by edge mask so the water/deck connects
 * across shared edges, possibly L↔R mirrored, unhandled shapes falling back to
 * the generic "00" tile), baked road-on-terrain art (T5 — a missing texture
 * falls through to the base tile + vector road overlay), then the coord-hashed
 * cosmetic variant of the base terrain. Shared by the map render loop and the
 * dock portrait so the portrait backdrop always matches the board.
 */
export function hexTileRef(
  h: Hex,
  terrainTex: LoadedTiles,
  riverCourse: Map<string, number[]>,
  roadTiles: Map<string, RoadTileRef>,
): PortraitTile {
  if (h.terrain === "river" || h.terrain === "bridge") {
    const oriented = orientedRiverTile(
      riverCourse.get(hexKey(h.coord.q, h.coord.r)) ?? [],
    );
    const name = oriented ? `${h.terrain}/${oriented.name}` : `${h.terrain}/00`;
    const variants =
      terrainTex.byOriented[name] ?? terrainTex.byOriented[`${h.terrain}/00`];
    const tex = variants[variantIndex(h.coord.q, h.coord.r, variants.length)];
    return { tex, mirror: oriented?.mirror ?? false };
  }
  const roadRef = h.road ? roadTiles.get(hexKey(h.coord.q, h.coord.r)) : undefined;
  const roadVariants =
    roadRef === undefined ? undefined : terrainTex.byOriented[roadRef.lookup];
  if (roadRef !== undefined && roadVariants !== undefined) {
    const tex = roadVariants[variantIndex(h.coord.q, h.coord.r, roadVariants.length)];
    return { tex, mirror: roadRef.mirror };
  }
  const variants = terrainTex.byTerrain[h.terrain];
  return {
    tex: variants[variantIndex(h.coord.q, h.coord.r, variants.length)],
    mirror: false,
  };
}
