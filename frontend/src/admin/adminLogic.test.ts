import { describe, expect, it } from "vitest";

import {
  confirmsDelete,
  errorText,
  DEFAULT_FORM,
  localTime,
  randomSeed,
  scheduleText,
  shortId,
  untilText,
  validateCreateForm,
} from "./adminLogic";

const now = new Date(2026, 10, 11, 7, 48);
const later = (minutes: number) => new Date(now.getTime() + minutes * 60_000);

describe("untilText", () => {
  it("words the time left in days, hours and minutes", () => {
    expect(untilText(later(5 * 60 + 12), now)).toBe("in 5h 12m");
    expect(untilText(later(3 * 1440 + 4 * 60 + 30), now)).toBe("in 3d 4h");
    expect(untilText(later(12), now)).toBe("in 12m");
    expect(untilText(later(60), now)).toBe("in 1h 0m");
  });

  it("says under a minute, then due now once the time has passed", () => {
    expect(untilText(new Date(now.getTime() + 30_000), now)).toBe("in under a minute");
    expect(untilText(now, now)).toBe("due now");
    expect(untilText(later(-90), now)).toBe("due now");
  });
});

describe("localTime", () => {
  it("gives the day and the time in the viewer's zone", () => {
    expect(localTime(new Date(2026, 10, 11, 13, 0), "en-US")).toBe("Wed, Nov 11, 1:00 PM");
  });
});

describe("scheduleText", () => {
  it("lists the clock's hours in the zone", () => {
    expect(scheduleText({ zone: "America/New_York", hours: [1, 13] })).toBe(
      "Scheduled ticks: 01:00 and 13:00 America/New_York",
    );
    expect(scheduleText({ zone: "UTC", hours: [0, 8, 16] })).toBe("Scheduled ticks: 00:00, 08:00 and 16:00 UTC");
  });
});

describe("validateCreateForm", () => {
  it("accepts the rehearsal defaults", () => {
    expect(validateCreateForm(DEFAULT_FORM)).toEqual({
      ok: true,
      body: { slots: 60, bots: 30, seed: 1, clock: "twice_daily" },
    });
  });

  it("accepts the edges the API accepts", () => {
    expect(validateCreateForm({ slots: "400", bots: "400", seed: "0", clock: "manual" }).ok).toBe(true);
    expect(validateCreateForm({ slots: " 1 ", bots: "0", seed: "7", clock: "manual" }).ok).toBe(true);
  });

  it("refuses slots outside 1 to 400 and non-numbers", () => {
    for (const slots of ["0", "401", "", "4.5", "-3", "abc"]) {
      const check = validateCreateForm({ ...DEFAULT_FORM, slots, bots: "0" });
      expect(check.ok, slots).toBe(false);
      if (!check.ok) expect(check.errors.slots).toMatch(/1 to 400/);
    }
  });

  it("refuses more bots than slots", () => {
    const check = validateCreateForm({ ...DEFAULT_FORM, slots: "4", bots: "5" });
    expect(check.ok).toBe(false);
    if (!check.ok) expect(check.errors.bots).toMatch(/cannot exceed slots/);
  });

  it("refuses a missing or negative seed", () => {
    for (const seed of ["", "-1", "1e3"]) {
      const check = validateCreateForm({ ...DEFAULT_FORM, seed });
      expect(check.ok, seed).toBe(false);
      if (!check.ok) expect(check.errors.seed).toBeDefined();
    }
  });
});

describe("delete confirmation", () => {
  const id = "5b87619e-ce84-4147-97ec-0748c09263ac";

  it("uses the id's first eight characters", () => {
    expect(shortId(id)).toBe("5b87619e");
  });

  it("is confirmed only by the short id, ignoring case and spaces", () => {
    expect(confirmsDelete(" 5B87619E ", id)).toBe(true);
    expect(confirmsDelete("5b87", id)).toBe(false);
    expect(confirmsDelete(id, id)).toBe(false);
    expect(confirmsDelete("", id)).toBe(false);
  });
});

describe("randomSeed", () => {
  it("gives a whole number the form accepts", () => {
    expect(randomSeed(() => 0.123456789)).toBe("123456");
    expect(validateCreateForm({ ...DEFAULT_FORM, seed: randomSeed() }).ok).toBe(true);
  });
});

describe("errorText", () => {
  it("shows the API's detail verbatim with the status", () => {
    expect(errorText(409, { detail: "game x is finished" })).toBe("HTTP 409: game x is finished");
  });

  it("puts each validation failure on its own line", () => {
    const detail = [
      { loc: ["body", "slots"], msg: "Input should be less than or equal to 400", type: "x" },
      { loc: ["body"], msg: "Value error, bots (5) cannot exceed slots (4)", type: "y" },
    ];
    expect(errorText(422, { detail })).toBe(
      "HTTP 422: slots: Input should be less than or equal to 400\nValue error, bots (5) cannot exceed slots (4)",
    );
  });

  it("keeps a network error's message", () => {
    expect(errorText(null, new TypeError("Failed to fetch"))).toBe("Failed to fetch");
    expect(errorText(500, "garbage")).toBe("HTTP 500: request failed");
  });
});
