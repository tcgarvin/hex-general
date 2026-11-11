import { describe, expect, it } from "vitest";

import { hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { hexInfo } from "./hexInfo";
import { decodeWorldMap, offsetToAxial } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];

const map = decodeWorldMap({
  width: 3,
  height: 1,
  terrain: "w.c",
  roads: ".##",
  sites: [
    { coord: offsetToAxial(1, 0), kind: "farm", garrison: 2, level: 3 },
    { coord: offsetToAxial(2, 0), kind: "city", garrison: 0, level: 1 },
  ],
});
const at = (col: number) => offsetToAxial(col, 0);
const unit = (id: number, owner: string, col: number, extra = {}) => ({
  id,
  owner,
  type: "infantry",
  coord: at(col),
  hp: 7,
  posture: "deployed",
  facing: "right",
  disordered: false,
  ...extra,
});
const state = {
  units: [unit(2, "neutral", 1), unit(1, "me", 0, { posture: "column" }), unit(3, "p7", 2, { disordered: true })],
  settlements: [
    { coord: at(1), controlled_by: null },
    { coord: at(2), controlled_by: "p7" },
  ],
} as unknown as WorldState;
const all = new Set([0, 1, 2].map((c) => hexKey(at(c).q, at(c).r)));

describe("hexInfo", () => {
  it("describes plain terrain and your units", () => {
    expect(hexInfo(map, state, all, "me", at(0))).toEqual({ title: "Woods", lines: ["Your infantry, 7/10 HP (column)"] });
  });

  it("names a leveled site, its garrison and defenders", () => {
    expect(hexInfo(map, state, all, "me", at(1))).toEqual({
      title: "Farm, level 3",
      lines: ["Neutral, garrison tier 2", "Garrison infantry, 7/10 HP"],
    });
  });

  it("keeps a city's terrain and road, and says who holds it", () => {
    expect(hexInfo(map, state, all, "me", at(2))).toEqual({
      title: "City",
      lines: ["City, road", "Held by p7", "p7's infantry, 7/10 HP (disordered)"],
    });
  });

  it("hides units out of sight", () => {
    expect(hexInfo(map, state, new Set(), "me", at(1)).lines).toEqual(["Neutral, garrison tier 2", "Not in sight"]);
  });
});
