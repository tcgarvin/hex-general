import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_PLAN, type WorldPlan } from "./plan";
import { clearUnsentPlan, loadUnsentPlan, parsePlan, saveUnsentPlan } from "./planStore";

/** A Map-backed stand-in for `sessionStorage` (the tests run without a DOM). */
function fakeStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    key: (i) => [...data.keys()][i] ?? null,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
    clear: () => data.clear(),
  };
}

const plan: WorldPlan = {
  orders: {
    3: { kind: "travel", destination: { q: 4, r: 0 }, via: [], onArrival: "column" },
    5: { kind: "move", path: [{ q: 1, r: 1 }] },
    6: { kind: "hold" },
  },
  recruits: { "0,0": { coord: { q: 0, r: 0 }, type: "infantry" } },
  build: "depot",
  disband: [5],
};

describe("unsent plan storage", () => {
  let store: Storage;
  beforeEach(() => {
    store = fakeStorage();
    vi.stubGlobal("window", { sessionStorage: store });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("restores a plan for the same world and turn only", () => {
    saveUnsentPlan("w1", 4, plan);
    expect(loadUnsentPlan("w1", 4)).toEqual(plan);
    expect(loadUnsentPlan("w2", 4)).toBeNull();
    clearUnsentPlan("w1", 4);
    expect(loadUnsentPlan("w1", 4)).toBeNull();
  });

  it("never restores an older turn's plan, and drops it", () => {
    saveUnsentPlan("w1", 3, plan);
    expect(loadUnsentPlan("w1", 4)).toBeNull();
    expect(store.length).toBe(0);
  });

  it("copes with storage that throws", () => {
    vi.stubGlobal("window", {
      get sessionStorage(): Storage {
        throw new Error("blocked");
      },
    });
    expect(() => saveUnsentPlan("w1", 4, plan)).not.toThrow();
    expect(loadUnsentPlan("w1", 4)).toBeNull();
  });
});

describe("parsePlan", () => {
  it("round-trips a plan and rejects anything else", () => {
    expect(parsePlan(JSON.stringify(plan))).toEqual(plan);
    expect(parsePlan(JSON.stringify(EMPTY_PLAN))).toEqual(EMPTY_PLAN);
    expect(parsePlan("not json")).toBeNull();
    expect(parsePlan(JSON.stringify({ ...plan, build: "castle" }))).toBeNull();
    expect(parsePlan(JSON.stringify({ ...plan, orders: { 1: { kind: "fly" } } }))).toBeNull();
  });
});
