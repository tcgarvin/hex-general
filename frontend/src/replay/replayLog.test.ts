import { describe, expect, it } from "vitest";

import { groupBeats } from "./beats";
import { buildReplayLog, nameLookup, type UnitRef } from "./replayLog";
import { events } from "./testFixtures";

const unitsById = new Map<number, UnitRef>([
  [1, { owner: "blue", type: "armor" }],
  [2, { owner: "red", type: "infantry" }],
]);
const nameOf = nameLookup(unitsById);

describe("buildReplayLog", () => {
  it("collapses unit_moved events into a per-beat summary, keeping other events", () => {
    const beats = groupBeats(
      events([
        { kind: "unit_moved", unit_id: 1, origin: { q: 0, r: 0 }, dest: { q: 1, r: 0 }, pulse: 1 },
        { kind: "unit_moved", unit_id: 2, origin: { q: 2, r: 0 }, dest: { q: 2, r: 1 }, pulse: 1 },
        {
          kind: "traffic_jam",
          coord: { q: 3, r: 3 },
          unit_ids: [3, 4],
          pulse: 1,
        },
      ]),
    );

    const log = buildReplayLog(beats, nameOf);

    expect(log).toHaveLength(1);
    expect(log[0].label).toBe("PULSE 1");
    expect(log[0].beatIndex).toBe(0);
    // The traffic-jam entry is expanded; the two moves collapse into one summary.
    const texts = log[0].entries.map((e) => e.lines[0].text);
    expect(texts).toEqual([
      "Traffic jam at (3,3): 2 friendly units collided and stopped.",
      "[2 units moved]",
    ]);
  });

  it("uses the singular form for a single move", () => {
    const beats = groupBeats(
      events([
        { kind: "unit_moved", unit_id: 1, origin: { q: 0, r: 0 }, dest: { q: 1, r: 0 }, pulse: 1 },
      ]),
    );
    const log = buildReplayLog(beats, nameOf);
    expect(log[0].entries[0].lines[0].text).toBe("[1 unit moved]");
  });

  it("maps each log beat back to its beat index for click-to-jump", () => {
    const beats = groupBeats(
      events([
        { kind: "barrage_resolved", artillery_id: 1, target: { q: 0, r: 0 }, hit: null },
        { kind: "unit_moved", unit_id: 1, origin: { q: 0, r: 0 }, dest: { q: 1, r: 0 }, pulse: 1 },
        { kind: "economy_resolved", player: "blue", income: 1, upkeep: 0, net: 1, lost_to_cap: 0, bank_before: 0, bank_after: 1 },
      ]),
    );
    const log = buildReplayLog(beats, nameOf);
    expect(log.map((b) => [b.beatIndex, b.label])).toEqual([
      [0, "BARRAGE"],
      [1, "PULSE 1"],
      [2, "ECONOMY"],
    ]);
  });
});
