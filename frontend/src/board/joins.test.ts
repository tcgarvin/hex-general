import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { axialToPixel, HEX_SIZE, type Axial } from "./hex";
import {
  compareJoins,
  groupJoinFiles,
  hasMouthArt,
  isEdgeJoin,
  isMouthJoin,
  JOIN_TERRAINS,
  joinableTerrains,
  joinDrawRank,
  joinPlacements,
  joinsOwnedBy,
  joinVariant,
  joinVariantIndex,
  MOUTH_ART,
  mouthPlacement,
  type JoinCode,
  type JoinPlacement,
} from "./joins";
import { riverMouths } from "./riverTiles";

type Terrain = components["schemas"]["Terrain"];

const CHARS: Record<string, Terrain> = { ".": "plains", w: "woods", c: "city", h: "hills", o: "lake", "~": "river" };

/** An ASCII map in odd-q offset rows (like scripts/board-mock-maps) → axial terrain probe. */
function asciiMap(text: string): {
  hexes: Axial[];
  terrainAt: (q: number, r: number) => Terrain | undefined;
} {
  const byKey = new Map<string, Terrain>();
  const hexes: Axial[] = [];
  text
    .trim()
    .split("\n")
    .forEach((line, row) => {
      [...line].forEach((ch, col) => {
        const a = { q: col, r: row - (col - (col & 1)) / 2 };
        hexes.push(a);
        byKey.set(`${a.q},${a.r}`, CHARS[ch]);
      });
    });
  return { hexes, terrainAt: (q, r) => byKey.get(`${q},${r}`) };
}

const JOINABLE: ReadonlySet<Terrain> = new Set<Terrain>(["woods", "city"]);

describe("joinVariantIndex", () => {
  // (q, r, code, count, expected), pinned from scripts/tests/test_joins.py
  // JOIN_HASH_CASES (captured there by running the joins README's JS).
  const cases: [number, number, JoinCode, number, number][] = [
    [0, 0, 0, 3, 0],
    [0, 0, 1, 3, 0],
    [0, 0, 2, 3, 1],
    [0, 0, 3, 3, 0],
    [0, 0, 4, 3, 1],
    [7, -3, 0, 3, 1],
    [7, -3, 2, 5, 2],
    [-4, 9, 1, 3, 1],
    [-4, 9, 4, 7, 3],
    [120, -60, 2, 3, 2],
    [39, -19, 3, 4, 3],
    [1000, 3000, 1, 3, 2],
    // river mouths, codes 5 + d
    [0, 0, 5, 3, 1],
    [0, 0, 10, 3, 1],
    [8, 0, 9, 3, 2],
    [1, 2, 5, 3, 0],
    [-4, 9, 7, 5, 1],
    [120, -60, 8, 3, 0],
    [39, -19, 6, 4, 0],
    [1000, 3000, 10, 7, 1],
  ];
  it.each(cases)("(%i, %i) code %i of %i -> %i, like board-mock.py", (q, r, code, count, expected) => {
    expect(joinVariantIndex(q, r, code, count)).toBe(expected);
  });

  it("returns 0 for a single variant", () => {
    expect(joinVariantIndex(5, 9, 2, 1)).toBe(0);
  });
});

describe("groupJoinFiles", () => {
  const kinds = ["edge_se", "edge_ne", "edge_n", "vert_e", "vert_ne"];
  const mouthKinds = ["mouth_se", "mouth_ne", "mouth_n", "mouth_nw", "mouth_sw", "mouth_s"];
  const noMouths = mouthKinds.map(() => []);

  it("groups by terrain and code, variants in filename order", () => {
    const entries = kinds.flatMap((k) =>
      ["01", "00"].map((n) => [`./joins/woods/${k}_${n}.png`, `${k}_${n}`] as const),
    );
    const groups = groupJoinFiles(entries);
    expect([...groups.keys()]).toEqual(["woods"]);
    expect(groups.get("woods")).toEqual([...kinds.map((k) => [`${k}_00`, `${k}_01`]), ...noMouths]);
  });

  it("groups any terrain's join files the same way (new join art needs no code change)", () => {
    const entries = ["woods", "hills"].flatMap((t) => kinds.map((k) => [`./joins/${t}/${k}_00.png`, `${t}/${k}`] as const));
    const groups = groupJoinFiles(entries);
    expect([...groups.keys()].sort()).toEqual(["hills", "woods"]);
    expect(groups.get("hills")).toEqual([...kinds.map((k) => [`hills/${k}`]), ...noMouths]);
    expect([...joinableTerrains(groups)].sort()).toEqual(["hills", "woods"]);
  });

  it("groups lake mouth sprites at codes 5 + d, like board-mock's JOIN_KINDS", () => {
    const entries = mouthKinds.flatMap((k) => ["01", "00"].map((n) => [`./joins/lake/${k}_${n}.png`, `${k}_${n}`] as const));
    const groups = groupJoinFiles(entries);
    expect(groups.get("lake")).toEqual([...kinds.map(() => []), ...mouthKinds.map((k) => [`${k}_00`, `${k}_01`])]);
    expect(hasMouthArt(groups)).toBe(true);
    expect(joinableTerrains(groups).size).toBe(0);
  });

  it("never treats lake as joinable, even with lake edge art", () => {
    const entries = kinds.map((k) => [`./joins/lake/${k}_00.png`, k] as const);
    const groups = groupJoinFiles(entries);
    expect(joinableTerrains(groups).has("lake")).toBe(false);
    expect(hasMouthArt(groups)).toBe(false);
  });

  it("rejects mouth sprites on a terrain other than lake", () => {
    expect(() => groupJoinFiles([["./joins/woods/mouth_s_00.png", 1]])).toThrow(/lake art only/);
  });

  it("rejects a partial mouth set", () => {
    const entries = mouthKinds.slice(1).map((k) => [`./joins/lake/${k}_00.png`, 1] as const);
    expect(() => groupJoinFiles(entries)).toThrow(/lake .*missing kinds: mouth_se$/);
  });

  it("rejects an unknown kind", () => {
    expect(() => groupJoinFiles([["./joins/woods/edge_sw_00.png", 1]])).toThrow(/kind must be/);
  });

  it("rejects a terrain missing a kind", () => {
    const entries = kinds.slice(1).map((k) => [`./joins/city/${k}_00.png`, 1] as const);
    expect(() => groupJoinFiles(entries)).toThrow(/city .*missing kinds: edge_se/);
  });

  it("ships woods and city joins, and lake river mouths", () => {
    expect([...JOIN_TERRAINS].sort()).toEqual(["city", "woods"]);
    expect(MOUTH_ART).toBe(true);
  });
});

describe("joinsOwnedBy / joinPlacements", () => {
  it("joins a same-terrain triangle with three edges and one vertex, like board-mock", () => {
    // scripts/tests/test_joins.py test_choose_joins_pairs_and_triangles.
    const { hexes, terrainAt } = asciiMap("ww..\nw...\n....\n...w");
    const chosen = joinPlacements(hexes, terrainAt, JOINABLE, HEX_SIZE, []);
    const edges = chosen.filter(isEdgeJoin);
    const vertices = chosen.filter((p) => !isEdgeJoin(p));
    expect(edges).toHaveLength(3);
    expect(vertices).toHaveLength(1);
    expect(chosen).toEqual([...edges, ...vertices]);

    for (const p of edges) {
      const [a, b] = p.members.map((m) => axialToPixel(m.q, m.r, HEX_SIZE));
      expect(p.x).toBeCloseTo((a.x + b.x) / 2);
      expect(p.y).toBeCloseTo((a.y + b.y) / 2);
    }
    const v = vertices[0];
    expect(v.members).toHaveLength(3);
    for (const m of v.members) {
      const c = axialToPixel(m.q, m.r, HEX_SIZE);
      expect(Math.hypot(c.x - v.x, c.y - v.y)).toBeCloseTo(HEX_SIZE);
    }
  });

  it("gives a lone pair one edge join and no vertex fill", () => {
    const { hexes, terrainAt } = asciiMap("w...\nw...\n....\n...w");
    const chosen = joinPlacements(hexes, terrainAt, JOINABLE, HEX_SIZE, []);
    expect(chosen).toHaveLength(1);
    expect(isEdgeJoin(chosen[0])).toBe(true);
    // (0,0) owns the edge to its south neighbour (0,1) as that hex's N edge.
    expect(chosen[0].owner).toEqual({ q: 0, r: 1 });
    expect(chosen[0].code).toBe(2);
  });

  it("only joins hexes of the same joinable terrain", () => {
    expect(joinPlacements(...args("wcwc"))).toEqual([]);
    expect(joinPlacements(...args("hh\nhh"))).toEqual([]);
    expect(joinPlacements(...args("..\n.."))).toEqual([]);
    expect(joinPlacements(...args("cc\ncc")).every((p) => p.terrain === "city")).toBe(true);
  });

  it("joins any terrain exactly like woods once it is joinable (art present)", () => {
    const woods = joinPlacements(...args("ww..\nw...\n....\n...w"));
    const hillsArgs = asciiMap("hh..\nh...\n....\n...h");
    const hills = joinPlacements(hillsArgs.hexes, hillsArgs.terrainAt, new Set<Terrain>(["hills"]), HEX_SIZE, []);
    expect(hills).toEqual(woods.map((p) => ({ ...p, terrain: "hills" })));
    // Without join art, hexes get no joins; lake hexes never get edge/vertex joins.
    expect(joinPlacements(hillsArgs.hexes, hillsArgs.terrainAt, JOINABLE, HEX_SIZE, [])).toEqual([]);
    const lakeArgs = asciiMap("oo..\no...\n....\n...o");
    expect(joinPlacements(lakeArgs.hexes, lakeArgs.terrainAt, JOIN_TERRAINS, HEX_SIZE, [])).toEqual([]);
  });

  it("names every edge and corner of a solid block exactly once", () => {
    const { hexes, terrainAt } = asciiMap("wwww\nwwww\nwwww\nwwww");
    const chosen = joinPlacements(hexes, terrainAt, JOINABLE, HEX_SIZE, []);
    const keys = new Set<string>();
    let adjacentPairs = 0;
    for (const p of chosen) keys.add(`${Math.round(p.x * 1000)},${Math.round(p.y * 1000)}`);
    expect(keys.size).toBe(chosen.length); // no two joins share an anchor
    for (const h of hexes) {
      for (const [dq, dr] of [[1, 0], [1, -1], [0, -1], [-1, 0], [-1, 1], [0, 1]]) {
        if (terrainAt(h.q + dq, h.r + dr) !== undefined) adjacentPairs++;
      }
    }
    expect(chosen.filter(isEdgeJoin)).toHaveLength(adjacentPairs / 2);
  });

  it("stops at the map border", () => {
    const { terrainAt } = asciiMap("w");
    expect(joinsOwnedBy({ q: 0, r: 0 }, terrainAt, JOINABLE, HEX_SIZE)).toEqual([]);
  });

  it("orders edges by (r, q, dir) before vertices, and the rank agrees", () => {
    const { hexes, terrainAt } = asciiMap("www\nwww\nwww");
    const chosen = joinPlacements(hexes, terrainAt, JOINABLE, HEX_SIZE, []);
    const shuffled = [...chosen].reverse();
    expect(shuffled.sort(compareJoins)).toEqual(chosen);
    const ranks = chosen.map(joinDrawRank);
    expect([...ranks].sort((a, b) => a - b)).toEqual(ranks);
    expect(new Set(ranks).size).toBe(ranks.length);
    // Negative coords (axial r goes negative on offset maps) still rank in order.
    const neg = { ...chosen[0], owner: { q: -3, r: -200 } };
    expect(joinDrawRank(neg)).toBeLessThan(joinDrawRank(chosen[0]));
  });
});

describe("joinVariant", () => {
  it("picks the owner/code-hashed variant of the placement's terrain", () => {
    const groups = new Map<Terrain, string[][]>([
      ["woods", [0, 1, 2, 3, 4].map((code) => ["a", "b", "c"].map((v) => `${code}${v}`))],
    ]);
    const { terrainAt } = asciiMap("ww\nww");
    for (const p of joinsOwnedBy({ q: 0, r: 0 }, terrainAt, JOINABLE, HEX_SIZE)) {
      const index = joinVariantIndex(0, 0, p.code, 3);
      expect(joinVariant(groups, p)).toBe(`${p.code}${"abc"[index]}`);
    }
  });
});

describe("river mouths", () => {
  // col 0: river rows 0-1 flowing south into lake rows 2-3, as board-mock's
  // test_lake_joins_and_mouth_sprite_are_chosen; woods to the side for edge joins.
  const { hexes, terrainAt } = asciiMap("~ww\n~ww\no..\no..");

  it("places a mouth on the river-lake edge, owned by the river hex", () => {
    const mouths = riverMouths(hexes, terrainAt);
    expect(mouths).toEqual([{ coord: { q: 0, r: 1 }, direction: 5, kind: "lake" }]);
    const p = mouthPlacement(mouths[0], HEX_SIZE);
    expect(p).toMatchObject({ terrain: "lake", owner: { q: 0, r: 1 }, code: 10, members: [{ q: 0, r: 1 }, { q: 0, r: 2 }] });
    const a = axialToPixel(0, 1, HEX_SIZE);
    const b = axialToPixel(0, 2, HEX_SIZE);
    expect(p.x).toBeCloseTo((a.x + b.x) / 2);
    expect(p.y).toBeCloseTo((a.y + b.y) / 2);
    expect(isMouthJoin(p)).toBe(true);
    expect(isEdgeJoin(p)).toBe(false);
  });

  it("rejects a map-edge outlet", () => {
    expect(() => mouthPlacement({ coord: { q: 0, r: 0 }, direction: 2, kind: "edge" }, HEX_SIZE)).toThrow(/not a lake mouth/);
  });

  it("draws mouths after every edge and vertex join, and the rank agrees", () => {
    const chosen = joinPlacements(hexes, terrainAt, JOINABLE, HEX_SIZE, riverMouths(hexes, terrainAt));
    const families = chosen.map((p) => (isEdgeJoin(p) ? "edge" : isMouthJoin(p) ? "mouth" : "vertex"));
    expect(families.at(-1)).toBe("mouth");
    expect(families.filter((f) => f === "mouth")).toHaveLength(1);
    expect(families.indexOf("vertex")).toBeGreaterThan(families.lastIndexOf("edge"));
    const ranks = chosen.map(joinDrawRank);
    expect([...ranks].sort((x, y) => x - y)).toEqual(ranks);
    // A mouth owned by the first hex still ranks after a vertex owned by the last.
    const lateVertex: JoinPlacement = { ...chosen[0], owner: { q: 999, r: 999 }, code: 4 };
    const earlyMouth: JoinPlacement = { ...chosen.at(-1)!, owner: { q: -999, r: -999 }, code: 10 };
    expect(compareJoins(lateVertex, earlyMouth)).toBeLessThan(0);
    expect(joinDrawRank(lateVertex)).toBeLessThan(joinDrawRank(earlyMouth));
  });

  it("hashes a mouth's variant with the river hex and code 5 + d", () => {
    const groups = new Map<Terrain, string[][]>([
      ["lake", [...Array(11).keys()].map((code) => (code < 5 ? [] : ["a", "b", "c"].map((v) => `${code}${v}`)))],
    ]);
    const p = mouthPlacement(riverMouths(hexes, terrainAt)[0], HEX_SIZE);
    expect(joinVariant(groups, p)).toBe(`10${"abc"[joinVariantIndex(0, 1, 10, 3)]}`);
  });
});

function args(
  text: string,
): [Axial[], (q: number, r: number) => Terrain | undefined, ReadonlySet<Terrain>, number, []] {
  const { hexes, terrainAt } = asciiMap(text);
  return [hexes, terrainAt, JOINABLE, HEX_SIZE, []];
}
