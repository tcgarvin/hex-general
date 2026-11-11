import { describe, expect, it } from "vitest";

import { unitBook } from "../turnReport";
import { casePlayback, FIXTURE_CASES } from "./testFixtures";
import { engagements } from "./watchList";

describe("your fights", () => {
  it("lists the viewer's combat, barrage and capture with beats to jump to", () => {
    const c = FIXTURE_CASES.find((x) => x.viewer === "b002" && x.turn === 6)!;
    const pb = casePlayback(c);
    const list = engagements(pb, unitBook(c.replay.events, c.replay.state_after.units, c.viewer));
    expect(list.map((e) => [e.kind, e.result])).toEqual([
      ["barrage", "shelled"],
      ["combat", "won"],
      ["capture", "took"],
    ]);
    const fight = list[1];
    expect(pb.beats[fight.beat].actions.some((a) => a.kind === "combat")).toBe(true);
    expect(fight.at).toEqual({ q: 25, r: 32 });
    expect(fight.ours.map((u) => u.unitId)).toEqual([9]);
    expect(fight.theirs).toMatchObject([{ unitId: 61, destroyed: true }]);
    expect(fight.summary).toBe("Your infantry took (25,32) from Garrison infantry.");
    expect(list.map((e) => e.beat)).toEqual([...list.map((e) => e.beat)].sort((a, b) => a - b));
  });

  it("skips fights the viewer only watched", () => {
    for (const c of FIXTURE_CASES) {
      const pb = casePlayback(c);
      for (const e of engagements(pb, unitBook(c.replay.events, c.replay.state_after.units, c.viewer))) {
        expect(e.kind === "capture" || e.ours.length > 0 || e.result === "shelled" || e.result === "missed").toBe(true);
      }
    }
  });
});
