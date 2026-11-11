import { describe, expect, it } from "vitest";

import { GLYPH_FILES, GLYPH_NAMES, glyphUrl } from "./glyphs";

describe("glyph set", () => {
  it("ships exactly one file per glyph name", () => {
    expect(GLYPH_FILES).toEqual([...GLYPH_NAMES].sort());
  });

  it("resolves every name to a URL", () => {
    for (const name of GLYPH_NAMES) expect(glyphUrl(name)).not.toBe("");
  });
});
