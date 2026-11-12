import { describe, expect, it } from "vitest";

import { unitBook } from "../turnReport";
import { casePlayback, FIXTURE_CASES } from "./testFixtures";
import { engagements, joinNames } from "./watchList";

describe("your fights", () => {
  it("lists the viewer's combat, barrage and capture with beats to jump to", () => {
    const c = FIXTURE_CASES.find((x) => x.viewer === "b001" && x.turn === 11)!;
    const pb = casePlayback(c);
    const list = engagements(pb, unitBook(c.replay.events, c.replay.state_after.units, c.viewer));
    expect(list.map((e) => [e.kind, e.result])).toEqual([
      ["barrage", "shelled"],
      ["combat", "won"],
      ["capture", "took"],
    ]);
    const fight = list[1];
    expect(pb.beats[fight.beat].actions.some((a) => a.kind === "combat")).toBe(true);
    expect(fight.at).toEqual({ q: 38, r: 20 });
    expect(fight.ours.map((u) => u.unitId)).toEqual([6]);
    expect(fight.theirs).toMatchObject([{ unitId: 52, destroyed: true }]);
    expect(fight.summary).toBe("Your infantry took (38,20) from Garrison infantry.");
    expect(list.map((e) => e.beat)).toEqual([...list.map((e) => e.beat)].sort((a, b) => a - b));
  });

  it("lists fire from a gun the viewer cannot see on their own unit", () => {
    const c = FIXTURE_CASES.find((x) => x.viewer === "b001" && x.turn === 16)!;
    const pb = casePlayback(c);
    const list = engagements(pb, unitBook(c.replay.events, c.replay.state_after.units, c.viewer));
    const unseen = c.replay.events.findIndex((e) => e.kind === "barrage_resolved" && e.artillery_id === null);
    expect(list.find((e) => e.eventIndex === unseen)).toMatchObject({
      kind: "barrage",
      result: "under_fire",
      at: { q: 21, r: 18 },
      ours: [{ unitId: 7 }],
      summary: "Your armor came under fire at (21,18).",
    });
  });

  it("skips fights the viewer only watched", () => {
    for (const c of FIXTURE_CASES) {
      const pb = casePlayback(c);
      for (const e of engagements(pb, unitBook(c.replay.events, c.replay.state_after.units, c.viewer))) {
        expect(e.kind === "capture" || e.ours.length > 0 || e.result === "shelled" || e.result === "missed").toBe(true);
      }
    }
  });

  it("names a side's owner once", () => {
    expect(joinNames(["Your infantry", "Your armor"])).toBe("Your infantry and armor");
    expect(joinNames(["Your infantry", "Your armor", "Your infantry"])).toBe("Your infantry, armor and infantry");
    expect(joinNames(["Garrison infantry", "s004 armor"])).toBe("Garrison infantry and s004 armor");
    expect(joinNames(["Your armor"])).toBe("Your armor");
    expect(joinNames(["1st Infantry", "1st Armor"])).toBe("1st Infantry and 1st Armor");
  });
});
