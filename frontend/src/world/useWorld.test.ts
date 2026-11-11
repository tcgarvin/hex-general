import { describe, expect, it } from "vitest";

import { pollDelay } from "./useWorld";

describe("pollDelay", () => {
  const now = Date.parse("2026-11-11T12:00:00Z");
  it("polls at the normal rate with no clock or a far tick", () => {
    expect(pollDelay(null, now)).toBe(10_000);
    expect(pollDelay("2026-11-11T13:00:00Z", now)).toBe(10_000);
  });

  it("wakes up for a tick due soon, and polls every 2 s once it is due", () => {
    expect(pollDelay("2026-11-11T12:00:05Z", now)).toBe(5_000);
    expect(pollDelay("2026-11-11T12:00:00Z", now)).toBe(2_000);
    expect(pollDelay("2026-11-11T11:59:00Z", now)).toBe(2_000);
  });
});
