import { describe, expect, it } from "vitest";

import { hexKey } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { attackPreview, barragePreview, settlementGroundAt } from "./combatView";
import { decodeWorldMap, offsetToAxial } from "./worldMap";

type WorldState = components["schemas"]["WorldState"];
type Unit = WorldState["units"][number];

// Plains, a farm site (held by s2), woods, hills.
const map = decodeWorldMap({
  width: 4,
  height: 1,
  terrain: "..wh",
  roads: "....",
  sites: [{ coord: offsetToAxial(1, 0), kind: "farm", garrison: 2, level: 1 }],
});
const at = (col: number) => offsetToAxial(col, 0);

function unit(id: number, owner: string, col: number, extra: Partial<Unit> = {}): Unit {
  return { id, owner, type: "infantry", coord: at(col), hp: 10, facing: "right", posture: "deployed", disordered: false, ...extra };
}

function state(units: Unit[]): WorldState {
  return { turn: 3, settlements: [{ coord: at(1), controlled_by: null }], units, players: [], resigned: [], standing_orders: [] } as unknown as WorldState;
}

const designate = (id: number) => (id === 1 ? "2nd Armor" : null);

describe("combat preview text", () => {
  it("knows a founded site's hex is settlement ground", () => {
    const s = state([]);
    expect(settlementGroundAt(map, s, at(1))).toBe(true);
    expect(settlementGroundAt(map, s, at(2))).toBe(false);
  });

  it("previews an assault on a garrison with its site and Hold bonus", () => {
    const me = unit(1, "me", 0, { type: "armor" });
    const p = attackPreview(me, at(1), map, state([me, unit(5, "neutral", 1)]), "me", designate);
    expect(p).toEqual({
      title: "Attack garrison infantry, 10 HP",
      lines: ["You deal 1 (floor 1), take 1 (floor 1).", "Defender: +2 site, +1 hold."],
      note: "If it stays put and nothing else joins: more attackers each strike, and it strikes each of them.",
    });
  });

  it("lists each unit a barrage would strike, and says when the hex is out of sight", () => {
    const s = state([unit(7, "s2", 2, { hp: 3 }), unit(8, "s2", 2, { posture: "column" })]);
    const visible = new Set([hexKey(at(2).q, at(2).r)]);
    expect(barragePreview(at(2), map, s, "me", visible, {}, designate).lines).toEqual([
      "s2 infantry, 3 HP: 1 (floor 1) · +2 woods, +1 if it holds",
      "s2 infantry, 10 HP: 3 · −3 packed column",
    ]);
    expect(barragePreview(at(3), map, s, "me", visible, {}, designate).lines).toEqual([
      "Not in sight: it hits whatever stands there.",
    ]);
  });
});
