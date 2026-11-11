import { describe, expect, it } from "vitest";

import { cycleUnit } from "./unitCycle";

const units = [{ id: 4 }, { id: 1 }, { id: 9 }, { id: 6 }];

describe("cycleUnit", () => {
  it("steps through units in id order and wraps", () => {
    const none = () => false;
    expect(cycleUnit(units, null, 1, none)?.id).toBe(1);
    expect(cycleUnit(units, 4, 1, none)?.id).toBe(6);
    expect(cycleUnit(units, 9, 1, none)?.id).toBe(1);
    expect(cycleUnit(units, 1, -1, none)?.id).toBe(9);
    expect(cycleUnit(units, 6, -1, none)?.id).toBe(4);
  });

  it("prefers units without orders", () => {
    const busy = (u: { id: number }) => u.id === 6 || u.id === 9;
    expect(cycleUnit(units, 4, 1, busy)?.id).toBe(1);
    expect(cycleUnit(units, 1, 1, busy)?.id).toBe(4);
  });

  it("falls back to every unit when none (or only the current one) is idle", () => {
    const allBusy = () => true;
    expect(cycleUnit(units, 4, 1, allBusy)?.id).toBe(6);
    const onlyFour = (u: { id: number }) => u.id !== 4;
    expect(cycleUnit(units, 4, 1, onlyFour)?.id).toBe(6);
  });

  it("returns null with no units", () => {
    expect(cycleUnit([], null, 1, () => false)).toBeNull();
  });
});
