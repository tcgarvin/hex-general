// Variety tiles (ratified 2026-11-11): occasional decoration of open country.
//
// Plains hexes now and then draw a tile with a small natural feature (trees,
// scrub, a pond, a hedgerow) or, beside a road or town, a homestead; plains road
// hexes now and then draw their road shape with buildings or trees beside it.
// Each comes in two sizes: small (round 1, `s`) and medium (round 2, `m`), a
// group covering about a quarter to a third of the hex. Files:
// `tiles/plains_nature/<s|m><NN>.png`, `tiles/plains_home/<s|m><NN>.png`,
// `tiles/road_variety/<shape>_<s|m><N>.png` (byte copies of
// `art/codex/terrain/candidates/variety-ratified/`).
//
// Purely visual: the choice reads only static map data (terrain, roads, sites),
// never units, ownership or fog, and the engine sees nothing. It is a port of
// `variety_picks` in `scripts/board-mock.py`, pinned by the parity fixture
// `__fixtures__/variety.json` (`scripts/variety-fixture.py`):
//
//   1. Site hexes are never decorated; a base counts with its full footprint.
//   2. Homestead candidates: plains hexes (no road, no site) next to a road or a
//      town/city, not next to a base hex, with roll(HOME) < homeTown (next to a
//      town/city) or < homeRoad.
//   3. Nature candidates: the other plains hexes with roll(NATURE) < nature.
//   4. Roadside candidates: plains road hexes whose baked road shape has variety
//      tiles, with roll(ROAD) < roadTown (a town/city within 2 hexes) or < roadOpen.
//   5. No clusters: a homestead candidate is kept only if it outranks every
//      adjacent homestead candidate (rank = roll(RANK), ties by q then r); nature
//      and roadside candidates rank as one pool and also yield to an adjacent
//      kept homestead, so no two decorated hexes touch.
//   6. Size: medium when roll(SIZE) < the mix's medium share, else small; a road
//      shape only one size has uses that size.
//   7. Variant: roll(PICK) over the size's files, stepped on past any file a
//      higher-ranked decorated hex within `repeatRadius` already uses (picks are
//      assigned in descending rank).
//
// Rolls are per mille: saltedIndex(q, r, salt, 1000). How much is decorated
// (density) and how much of it is medium (mix) are set in `varietySettings.json`.

import { AXIAL_DIRECTIONS, hexKey, type Axial, type Hex } from "./hex";
import type { RoadTileRef } from "./roadTiles";
import settings from "./varietySettings.json";

export type VarietyKind = "nature" | "home" | "road";
export type VarietySize = "small" | "medium";

/** Per-mille candidate probabilities (before the no-cluster rule). */
export interface VarietyDensity {
  readonly nature: number;
  readonly homeRoad: number;
  readonly homeTown: number;
  readonly roadOpen: number;
  readonly roadTown: number;
}

export const VARIETY_DENSITIES: Readonly<Record<string, VarietyDensity>> = settings.densities;
/** Medium share per mille of decorated hexes, by mix name. */
export const VARIETY_MIXES: Readonly<Record<string, number>> = settings.mixes;

/** What the game draws: the density and mix named in `varietySettings.json`. */
export const SHIPPED_VARIETY = {
  density: VARIETY_DENSITIES[settings.density],
  mediumShare: VARIETY_MIXES[settings.mix],
  repeatRadius: settings.repeatRadius,
} as const;

const SALT_NATURE = 11;
const SALT_HOME = 12;
const SALT_ROAD = 13;
const SALT_RANK = 14;
const SALT_PICK = 15;
const SALT_SIZE = 16;
const SETTLEMENTS: ReadonlySet<string> = new Set(["town", "city"]);

/** Tile folders under `tiles/`. */
const FOLDERS: Readonly<Record<VarietyKind, string>> = {
  nature: "plains_nature",
  home: "plains_home",
  road: "road_variety",
};

/** One size's variety files, as `<folder>/<stem>` keys in locale order. */
export interface VarietySet {
  readonly nature: readonly string[];
  readonly home: readonly string[];
  /** Road oriented shape (`straight_v`, `fork_se_n_sw`, ...) -> files. */
  readonly road: Readonly<Record<string, readonly string[]>>;
}

export interface VarietyCatalog {
  readonly small: VarietySet | null;
  readonly medium: VarietySet | null;
}

/** A decorated hex: which tile file it draws (`byOriented` key) and why. */
export interface VarietyPick {
  readonly kind: VarietyKind;
  readonly size: VarietySize;
  /** `<folder>/<stem>`, e.g. `plains_home/m03`, `road_variety/straight_d_s2`. */
  readonly key: string;
}

/** Hexes no variety tile may draw on: every site hex, and the bases' footprints. */
export interface VarietyExclusions {
  readonly covered: ReadonlySet<string>;
  readonly bases: ReadonlySet<string>;
}

/**
 * The variety catalog from tile glob keys (`./tiles/<folder>/<file>.png`),
 * sorted like `groupTileFiles` (locale order) so each index means the same file
 * as in `scripts/board-mock.py`. A size with no plains_nature or plains_home
 * files is `null`; a misnamed variety file throws.
 */
export function varietyCatalog(tileKeys: readonly string[]): VarietyCatalog {
  const sorted = [...tileKeys].sort((a, b) => a.localeCompare(b));
  const build = (tag: "s" | "m"): VarietySet | null => {
    const nature: string[] = [];
    const home: string[] = [];
    const road: Record<string, string[]> = {};
    for (const key of sorted) {
      const parts = key.split("/");
      if (parts.length !== 4) continue;
      const [, , folder, file] = parts;
      const stem = file.replace(/\.png$/, "");
      if (folder === FOLDERS.road) {
        const m = /^([a-z_]+)_([sm])\d+$/.exec(stem);
        if (m === null) throw new Error(`misnamed road variety tile: ${key}`);
        if (m[2] === tag) (road[m[1]] ??= []).push(`${folder}/${stem}`);
      } else if (folder === FOLDERS.nature || folder === FOLDERS.home) {
        if (!/^[sm]\d+$/.test(stem)) throw new Error(`misnamed variety tile: ${key}`);
        if (stem.startsWith(tag)) (folder === FOLDERS.nature ? nature : home).push(`${folder}/${stem}`);
      }
    }
    if (nature.length === 0 && home.length === 0) return null;
    if (nature.length === 0 || home.length === 0) {
      throw new Error(`variety size "${tag}" needs both nature and home tiles`);
    }
    return { nature, home, road };
  };
  return { small: build("s"), medium: build("m") };
}

/**
 * Site hexes as variety exclusions: every site's hex, plus each base's full
 * footprint (`baseFootprint` offsets from its anchor) in both sets, whatever
 * stage it is at, since the decoration is static and a base may grow.
 */
export function varietyExclusions(
  sites: readonly { readonly kind: string; readonly coord: Axial }[],
  baseFootprint: readonly Axial[],
): VarietyExclusions {
  const covered = new Set<string>();
  const bases = new Set<string>();
  for (const site of sites) {
    if (site.kind !== "base") {
      covered.add(hexKey(site.coord.q, site.coord.r));
      continue;
    }
    for (const o of baseFootprint) {
      const key = hexKey(site.coord.q + o.q, site.coord.r + o.r);
      covered.add(key);
      bases.add(key);
    }
  }
  return { covered, bases };
}

/**
 * A stable value in `[0, count)` per (hex, salt): `variantIndex` with a third
 * hash term (as `joinVariantIndex`). Bit for bit `salted_index` in board-mock.
 */
export function saltedIndex(q: number, r: number, salt: number, count: number): number {
  if (count <= 1) return 0;
  let h = (q | 0) * 0x1f1f1f1f + (r | 0) * 0x85ebca6b + salt * 0x27d4eb2f;
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  return Math.abs(h) % count;
}

const roll = (q: number, r: number, salt: number): number => saltedIndex(q, r, salt, 1000);

/** `a` outranks `b`: higher rank roll, ties by q then r (board-mock `_rank`). */
function outranksHex(a: Axial, b: Axial): boolean {
  const ra = roll(a.q, a.r, SALT_RANK);
  const rb = roll(b.q, b.r, SALT_RANK);
  if (ra !== rb) return ra > rb;
  if (a.q !== b.q) return a.q > b.q;
  return a.r > b.r;
}

const isRoadLike = (h: Hex | undefined): boolean => h !== undefined && (h.road || h.terrain === "bridge");

/**
 * The decorated hexes of a map, keyed by `hexKey`: computed once per map from
 * static data. `roadTiles` is `roadTileRefs` over the same map; `hexAt` returns
 * `undefined` off the map. Throws when a decorated hex has no tile to draw (a
 * catalog with neither size).
 */
export function varietyPicks(
  hexes: readonly Hex[],
  hexAt: (q: number, r: number) => Hex | undefined,
  roadTiles: ReadonlyMap<string, RoadTileRef>,
  exclusions: VarietyExclusions,
  catalog: VarietyCatalog,
  density: VarietyDensity = SHIPPED_VARIETY.density,
  mediumShare: number = SHIPPED_VARIETY.mediumShare,
  repeatRadius: number = SHIPPED_VARIETY.repeatRadius,
): Map<string, VarietyPick> {
  const picks = new Map<string, VarietyPick>();
  if (catalog.small === null && catalog.medium === null) return picks;
  const sizes: Record<VarietySize, VarietySet | null> = { small: catalog.small, medium: catalog.medium };
  const roadShapes = new Set<string>();
  for (const set of [catalog.small, catalog.medium]) {
    if (set !== null) for (const shape of Object.keys(set.road)) roadShapes.add(shape);
  }
  const settlementWithin = (a: Axial, radius: number): boolean => {
    for (let dq = -radius; dq <= radius; dq++) {
      for (let dr = Math.max(-radius, -dq - radius); dr <= Math.min(radius, -dq + radius); dr++) {
        const n = hexAt(a.q + dq, a.r + dr);
        if (n !== undefined && SETTLEMENTS.has(n.terrain)) return true;
      }
    }
    return false;
  };

  // Internal bookkeeping uses numeric hex ids (no per-hex string keys); the
  // result is keyed by hexKey like the renderer's other per-map tables.
  const id = (q: number, r: number): number => (q + 0x8000) * 0x10000 + (r + 0x8000);
  const toIds = (keys: ReadonlySet<string>): Set<number> =>
    new Set([...keys].map((k) => {
      const [q, r] = k.split(",").map(Number);
      return id(q, r);
    }));
  const covered = toIds(exclusions.covered);
  const bases = toIds(exclusions.bases);

  const homeC = new Map<number, Axial>();
  const natureC = new Map<number, Axial>();
  const roadC = new Map<number, { coord: Axial; shape: string }>();
  for (const h of hexes) {
    if (h.terrain !== "plains") continue;
    const { q, r } = h.coord;
    const hid = id(q, r);
    if (covered.has(hid)) continue;
    if (h.road) {
      const ref = roadTiles.get(hexKey(q, r));
      if (ref === undefined) continue;
      const shape = ref.lookup.replace(/^road\/plains_/, "");
      if (!roadShapes.has(shape)) continue;
      const p = settlementWithin(h.coord, 2) ? density.roadTown : density.roadOpen;
      if (roll(q, r, SALT_ROAD) < p) roadC.set(hid, { coord: h.coord, shape });
      continue;
    }
    // The home roll first: most hexes cannot be candidates, so skip the scan.
    const homeRoll = roll(q, r, SALT_HOME);
    let nearTown = false;
    let nearRoad = false;
    let nearBase = false;
    if (homeRoll < Math.max(density.homeTown, density.homeRoad)) {
      for (const d of AXIAL_DIRECTIONS) {
        const n = hexAt(q + d.q, r + d.r);
        if (n !== undefined && SETTLEMENTS.has(n.terrain)) nearTown = true;
        if (isRoadLike(n)) nearRoad = true;
        if (bases.has(id(q + d.q, r + d.r))) nearBase = true;
      }
    }
    if ((nearTown || nearRoad) && !nearBase) {
      const p = nearTown ? density.homeTown : density.homeRoad;
      if (homeRoll < p) {
        homeC.set(hid, h.coord);
        continue;
      }
    }
    if (roll(q, r, SALT_NATURE) < density.nature) natureC.set(hid, h.coord);
  }

  const outranks = (at: Axial, rivals: ReadonlyMap<number, unknown>): boolean => {
    for (const d of AXIAL_DIRECTIONS) {
      const n = { q: at.q + d.q, r: at.r + d.r };
      if (rivals.has(id(n.q, n.r)) && !outranksHex(at, n)) return false;
    }
    return true;
  };
  const touches = (at: Axial, set: ReadonlyMap<number, unknown>): boolean =>
    AXIAL_DIRECTIONS.some((d) => set.has(id(at.q + d.q, at.r + d.r)));

  const homes = new Map([...homeC].filter(([, at]) => outranks(at, homeC)));
  const others = new Map<number, Axial>(natureC);
  for (const [hid, c] of roadC) others.set(hid, c.coord);
  const chosen: { coord: Axial; kind: VarietyKind; shape: string }[] = [];
  for (const at of homes.values()) chosen.push({ coord: at, kind: "home", shape: "" });
  for (const [hid, at] of others) {
    if (!outranks(at, others) || touches(at, homes)) continue;
    const road = roadC.get(hid);
    chosen.push(road === undefined ? { coord: at, kind: "nature", shape: "" } : { coord: at, kind: "road", shape: road.shape });
  }
  // Rank order (descending) fixes which hex keeps a variant when two are near.
  const rankOf = new Map(chosen.map((c) => [c, roll(c.coord.q, c.coord.r, SALT_RANK)]));
  chosen.sort((a, b) => rankOf.get(b)! - rankOf.get(a)! || b.coord.q - a.coord.q || b.coord.r - a.coord.r);

  const used = new Map<number, string>(); // hex id -> file key, for the no-repeat rule
  for (const { coord, kind, shape } of chosen) {
    const has = (set: VarietySet): boolean => kind !== "road" || shape in set.road;
    let size: VarietySize =
      catalog.medium !== null && roll(coord.q, coord.r, SALT_SIZE) < mediumShare ? "medium" : "small";
    const want = sizes[size];
    if (want === null || !has(want)) size = size === "medium" ? "small" : "medium";
    const set = sizes[size];
    if (set === null) throw new Error(`no ${kind} variety tiles for hex ${hexKey(coord.q, coord.r)}`);
    const files = kind === "road" ? set.road[shape] : set[kind];
    const near = new Set<string>();
    for (let dq = -repeatRadius; dq <= repeatRadius; dq++) {
      const lo = Math.max(-repeatRadius, -dq - repeatRadius);
      const hi = Math.min(repeatRadius, -dq + repeatRadius);
      for (let dr = lo; dr <= hi; dr++) {
        const other = used.get(id(coord.q + dq, coord.r + dr));
        if (other !== undefined) near.add(other);
      }
    }
    const start = saltedIndex(coord.q, coord.r, SALT_PICK, files.length);
    let index = start;
    for (let k = 0; k < files.length; k++) {
      const i = (start + k) % files.length;
      if (!near.has(files[i])) {
        index = i;
        break;
      }
    }
    used.set(id(coord.q, coord.r), files[index]);
    picks.set(hexKey(coord.q, coord.r), { kind, size, key: files[index] });
  }
  return picks;
}
