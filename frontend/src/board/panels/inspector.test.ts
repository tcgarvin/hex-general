import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";

import { inspectorContent } from "./inspector";

type Hex = components["schemas"]["Hex"];
type Terrain = components["schemas"]["Terrain"];
type Unit = components["schemas"]["Unit"];

const hex = (terrain: Terrain, road = false): Hex => ({
  coord: { q: 15, r: 22 },
  terrain,
  road,
});

const unit = (owner: string, type: Unit["type"], hp: number): Unit => ({
  id: 1,
  owner,
  type,
  coord: { q: 15, r: 22 },
  hp,
  facing: "left",
});

describe("inspectorContent", () => {
  it("describes a plain terrain hex (UI-SPEC §7 woods sample)", () => {
    const c = inspectorContent(hex("woods"), null, null);
    expect(c.title).toBe("WOODS");
    expect(c.coord).toBe("(15,22)");
    expect(c.lines).toEqual([
      "Defense +2",
      "Move cost 1",
      "Blocks LOS: yes",
      "Road: no",
    ]);
  });

  it("describes a controlled city (economy block, recruit, control)", () => {
    const c = inspectorContent(hex("city"), { owner: "blue" }, null);
    expect(c.title).toBe("CITY");
    expect(c.lines).toEqual([
      "Defense +2",
      "Muster +3",
      "Repair +2 (resting)",
      "Recruit: yes",
      "Control: Blue",
    ]);
  });

  it("describes a town without a recruit line and marks neutral control", () => {
    const c = inspectorContent(hex("town"), { owner: null }, null);
    expect(c.lines).toContain("Muster +2");
    expect(c.lines).not.toContain("Recruit: yes");
    expect(c.lines).toContain("Control: Neutral");
  });

  it("marks rivers impassable and bridges as crossings", () => {
    expect(inspectorContent(hex("river"), null, null).lines).toEqual([
      "Defense +0",
      "Impassable",
      "Road: no",
    ]);
    expect(inspectorContent(hex("bridge"), null, null).lines).toContain(
      "Passable river crossing",
    );
  });

  it("notes the road layer, including on settlements that have one", () => {
    expect(inspectorContent(hex("plains", true), null, null).lines).toContain(
      "Road: yes",
    );
    const town = inspectorContent(hex("town", true), { owner: "red" }, null);
    expect(town.lines).toContain("Road: yes");
  });

  it("appends the visible occupant's known-unit line (UI-SPEC §9)", () => {
    const c = inspectorContent(hex("hills"), null, unit("red", "artillery", 6));
    expect(c.lines[0]).toBe("Defense +1");
    expect(c.lines[c.lines.length - 1]).toBe("Unit: Red Artillery 6/10");
  });
});
