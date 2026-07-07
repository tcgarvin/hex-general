import { describe, expect, it } from "vitest";

import { BOTTOM_HEIGHT } from "@/board/layout";
import type { components } from "@/lib/api/schema";

import {
  BTN_H,
  BTN_W,
  CONTENT_X,
  paletteButtonAt,
  paletteButtonRect,
  paletteButtons,
  PORTRAIT_SIZE,
  PORTRAIT_X,
  PORTRAIT_Y,
} from "./selectedUnit";

type Unit = components["schemas"]["Unit"];

const unit = (type: Unit["type"]): Unit => ({
  id: 1,
  owner: "p1",
  type,
  coord: { q: 0, r: 0 },
  hp: 10,
  facing: "left",
});

describe("portrait geometry", () => {
  it("fits the square portrait inside the bottom strip with its margins", () => {
    expect(PORTRAIT_Y + PORTRAIT_SIZE + PORTRAIT_Y).toBe(BOTTOM_HEIGHT);
  });

  it("keeps the text/palette column clear of the portrait box", () => {
    expect(CONTENT_X).toBeGreaterThan(PORTRAIT_X + PORTRAIT_SIZE);
  });
});

describe("paletteButtonRect / paletteButtonAt", () => {
  it("lays buttons out from CONTENT_X and hit-tests them round-trip", () => {
    const inf = unit("infantry");
    paletteButtons(inf).forEach((b, i) => {
      const r = paletteButtonRect(i);
      expect(r.x).toBeGreaterThanOrEqual(CONTENT_X);
      expect(
        paletteButtonAt(r.x + r.width / 2, r.y + r.height / 2, inf),
      ).toBe(b.action);
    });
  });

  it("does not hit buttons over the portrait box or in gaps", () => {
    const inf = unit("infantry");
    const midY = paletteButtonRect(0).y + BTN_H / 2;
    // Over the portrait (left of the palette).
    expect(paletteButtonAt(PORTRAIT_X + 10, midY, inf)).toBeNull();
    // In the gap between the first two buttons.
    expect(paletteButtonAt(CONTENT_X + BTN_W + 2, midY, inf)).toBeNull();
    // Above the button row.
    expect(paletteButtonAt(CONTENT_X + 10, midY - BTN_H, inf)).toBeNull();
  });

  it("gates the Barrage button to artillery", () => {
    const actions = (u: Unit) => paletteButtons(u).map((b) => b.action);
    expect(actions(unit("infantry"))).toEqual(["move", "rest", "hold", "clear"]);
    expect(actions(unit("artillery"))).toEqual([
      "move",
      "barrage",
      "rest",
      "hold",
      "clear",
    ]);
  });
});
