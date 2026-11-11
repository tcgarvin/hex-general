import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_PLAN, NO_SUBMISSION, planKey, submissionKey, toSubmission, type WorldPlan } from "./plan";
import {
  clearUnsentPlan,
  draftOnLoad,
  loadUnsentPlan,
  parseDraft,
  parsePlan,
  saveUnsentPlan,
  supersededBy,
} from "./planStore";

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
    saveUnsentPlan("w1", 4, { plan, base: "abc" });
    expect(loadUnsentPlan("w1", 4)).toEqual({ plan, base: "abc" });
    expect(loadUnsentPlan("w2", 4)).toBeNull();
    clearUnsentPlan("w1", 4);
    expect(loadUnsentPlan("w1", 4)).toBeNull();
  });

  it("never restores an older turn's plan, and drops it", () => {
    saveUnsentPlan("w1", 3, { plan, base: NO_SUBMISSION });
    expect(loadUnsentPlan("w1", 4)).toBeNull();
    expect(store.length).toBe(0);
  });

  it("copes with storage that throws", () => {
    vi.stubGlobal("window", {
      get sessionStorage(): Storage {
        throw new Error("blocked");
      },
    });
    expect(() => saveUnsentPlan("w1", 4, { plan, base: NO_SUBMISSION })).not.toThrow();
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

describe("a draft and the server plan it was forked from", () => {
  const newer: WorldPlan = { ...EMPTY_PLAN, orders: { 6: { kind: "rest" } } };

  it("identifies a server plan by its content, not its order of keys", () => {
    const submission = toSubmission(plan, "s1");
    const shuffled = { ...submission, orders: [...(submission.orders ?? [])].reverse() };
    expect(submissionKey(shuffled, "s1")).toBe(submissionKey(submission, "s1"));
    expect(submissionKey(submission, "s1")).toBe(planKey(plan, "s1"));
    expect(submissionKey(toSubmission(newer, "s1"), "s1")).not.toBe(planKey(plan, "s1"));
    expect(submissionKey(null, "s1")).toBe(NO_SUBMISSION);
    // An empty submission is still a submission.
    expect(submissionKey(toSubmission(EMPTY_PLAN, "s1"), "s1")).not.toBe(NO_SUBMISSION);
  });

  it("restores a draft over the server plan it was forked from", () => {
    const base = planKey(newer, "s1");
    expect(draftOnLoad({ plan, base }, base)).toEqual({ restore: plan, setAside: null });
    expect(draftOnLoad({ plan, base: NO_SUBMISSION }, NO_SUBMISSION)).toEqual({ restore: plan, setAside: null });
  });

  it("sets a draft aside when a newer plan was submitted elsewhere", () => {
    // Forked when nothing was submitted; another device has since sent `newer`.
    expect(draftOnLoad({ plan, base: NO_SUBMISSION }, planKey(newer, "s1"))).toEqual({ restore: null, setAside: plan });
    // Forked from one plan; another was sent over it.
    expect(draftOnLoad({ plan, base: planKey(EMPTY_PLAN, "s1") }, planKey(newer, "s1"))).toEqual({
      restore: null,
      setAside: plan,
    });
  });

  it("sets aside a draft from an older build, which recorded no base", () => {
    const old = parseDraft(JSON.stringify(plan));
    expect(old).toEqual({ plan, base: "" });
    expect(draftOnLoad(old, NO_SUBMISSION)).toEqual({ restore: null, setAside: plan });
  });

  it("has nothing to do without a stored draft", () => {
    expect(draftOnLoad(null, NO_SUBMISSION)).toEqual({ restore: null, setAside: null });
  });

  it("sets live edits aside only when the server plan changed under unsent edits", () => {
    const base = planKey(EMPTY_PLAN, "s1");
    const other = planKey(newer, "s1");
    expect(supersededBy({ base, sent: false, dirty: true }, other)).toBe(true);
    expect(supersededBy({ base, sent: false, dirty: true }, base)).toBe(false);
    // This tab's own submit just landed: never set aside.
    expect(supersededBy({ base, sent: true, dirty: true }, other)).toBe(false);
    // Nothing unsent to lose.
    expect(supersededBy({ base, sent: false, dirty: false }, other)).toBe(false);
  });
});
