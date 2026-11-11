import { describe, expect, it } from "vitest";

import { worldName, worldTag } from "./worldName";

const ID = "3f2a9c1e-8b7d-4e5f-a1b2-c3d4e5f6a7b8";

describe("worldName", () => {
  it("gives the same id the same name every time", () => {
    expect(worldName(ID)).toBe(worldName(ID));
  });

  // Pinned so a change to the word lists or hash, which would rename every
  // running world under its players, is a deliberate test update.
  it("keeps known ids on their known names", () => {
    expect(worldName(ID)).toBe("Old Barrens");
    expect(worldName("00000000-0000-0000-0000-000000000000")).toBe("Bitter Glen");
  });

  it("names a world with two capitalized words", () => {
    expect(worldName(ID)).toMatch(/^[A-Z][a-z]+ [A-Z][a-z]+$/);
  });

  it("gives ids differing in one character different names", () => {
    expect(worldName("3f2a9c1e-8b7d-4e5f-a1b2-c3d4e5f6a7b9")).not.toBe(worldName(ID));
  });

  it("spreads a batch of ids over many names", () => {
    const ids = Array.from(
      { length: 40 },
      (_, i) => `${i.toString(16).padStart(8, "0")}-8b7d-4e5f-a1b2-c3d4e5f6a7b8`,
    );
    expect(new Set(ids.map(worldName)).size).toBeGreaterThan(30);
  });
});

describe("worldTag", () => {
  it("is the id's first four characters, uppercased", () => {
    expect(worldTag(ID)).toBe("3F2A");
  });
});
