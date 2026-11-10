// Column (packed, travelling) unit sprites for the world board (W6).
//
// Same two-layer format as the deployed sprites (`@/board/units`): a neutral base
// and a tintable team mask, 128×128, facing west, built by unit.py's split from
// Codex candidates (provenance: art/codex/columns/). Ratified per type, so the set
// can be partial: a type without art keeps the plain column token.
//
// Ratified (2026-11-09):
//   infantry  = troop truck (infantry-sheet-1/inf-truck-2)
//   armor     = armoured-cab tank transporter (armor-transporter-1/arm-tt-c-w120)
//   artillery = tracked tractor towing the gun (artillery-sheet-1/art-tractor-1)

import { Assets, type Texture } from "pixi.js";

import type { UnitSpritePair } from "@/board/units";
import type { components } from "@/lib/api/schema";

type UnitType = components["schemas"]["UnitType"];

const UNIT_TYPES: readonly UnitType[] = ["infantry", "artillery", "armor"];

const COLUMN_MODULES = import.meta.glob<string>("./columns/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

export type LoadedColumns = Partial<Record<UnitType, UnitSpritePair>>;

/** Load every column sprite pair that ships; a half-shipped pair is an error. */
export async function loadColumnTextures(): Promise<LoadedColumns> {
  const loaded: LoadedColumns = {};
  await Promise.all(
    UNIT_TYPES.map(async (type) => {
      const baseUrl = COLUMN_MODULES[`./columns/${type}_base.png`];
      const maskUrl = COLUMN_MODULES[`./columns/${type}_mask.png`];
      if (baseUrl === undefined && maskUrl === undefined) return;
      if (baseUrl === undefined || maskUrl === undefined) {
        throw new Error(`column sprite for ${type} is missing a layer`);
      }
      const [base, mask] = await Promise.all([Assets.load<Texture>(baseUrl), Assets.load<Texture>(maskUrl)]);
      base.source.scaleMode = "linear";
      mask.source.scaleMode = "linear";
      loaded[type] = { base, mask };
    }),
  );
  return loaded;
}
