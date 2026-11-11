import { describe, expect, it } from "vitest";

import { clockText } from "./worldClock";

// Local times, so the test reads the same in any zone.
const now = new Date(2026, 10, 11, 6, 30);
const today = new Date(2026, 10, 11, 7, 1).toISOString();
const tomorrow = new Date(2026, 10, 12, 13, 0).toISOString();

describe("clockText", () => {
  it("words a manual world with an announced resolve without contradicting itself", () => {
    expect(clockText("manual", today, now, "en-US")).toBe("Next resolve 7:01 AM (driven manually)");
    expect(clockText("manual", null, now, "en-US")).toBe("Manual clock: resolves when ticked");
  });

  it("gives a scheduled world its next resolve, with the day when it isn't today", () => {
    expect(clockText("twice_daily", tomorrow, now, "en-US")).toBe("Twice daily · next resolve Thu, Nov 12, 1:00 PM");
    expect(clockText("twice_daily", null, now, "en-US")).toBe("Twice daily");
  });
});
