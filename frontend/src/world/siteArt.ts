// Painted site-kind icons (ratified 2026-11-09: Codex painted register, sheet 3,
// variant 1; provenance in art/codex/sites/). 128×128 RGBA, the emblem centred
// on a round vignette, drawn over the site's hex at tactical zoom and in panels.

import { Assets, type Texture } from "pixi.js";

import type { SiteKind } from "./worldMap";

const SITE_KINDS: readonly SiteKind[] = ["base", "city", "farm", "oil_field", "steel_mill", "factory"];

const SITE_MODULES = import.meta.glob<string>("./sites/*.png", {
  eager: true,
  query: "?url",
  import: "default",
});

/** The icon's URL, for `<img>` in panels. Throws if the art is missing. */
export function siteIconUrl(kind: SiteKind): string {
  const url = SITE_MODULES[`./sites/${kind}.png`];
  if (url === undefined) throw new Error(`missing site icon for ${kind}`);
  return url;
}

export type LoadedSites = Record<SiteKind, Texture>;

/** Load all six site textures; rejects if any is missing or fails to load. */
export async function loadSiteTextures(): Promise<LoadedSites> {
  const entries = await Promise.all(
    SITE_KINDS.map(async (kind) => {
      const texture = await Assets.load<Texture>(siteIconUrl(kind));
      texture.source.scaleMode = "linear";
      return [kind, texture] as const;
    }),
  );
  return Object.fromEntries(entries) as LoadedSites;
}
