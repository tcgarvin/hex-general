import { describe, expect, it } from "vitest";

import { BOTTOM_HEIGHT } from "@/board/layout";
import type { components } from "@/lib/api/schema";

import {
  BTN_H,
  BTN_W,
  BTN_X0,
  CARD_H,
  CARD_W,
  CARD_X,
  CARD_Y,
  CONTENT_W,
  CONTENT_X,
  paletteButtonAt,
  paletteButtonRect,
  paletteButtons,
  PORTRAIT_SIZE,
  PORTRAIT_X,
  SHEET_EDGE,
  WELL_SIZE,
  WELL_X,
  WELL_Y,
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

describe("card geometry", () => {
  it("fits the card inside the bottom strip", () => {
    expect(CARD_Y + CARD_H).toBeLessThanOrEqual(BOTTOM_HEIGHT);
  });

  it("keeps the portrait well inside the sheet's parchment face", () => {
    expect(WELL_X).toBeGreaterThanOrEqual(CARD_X + SHEET_EDGE);
    expect(WELL_Y).toBeGreaterThanOrEqual(CARD_Y + SHEET_EDGE);
    expect(WELL_Y + WELL_SIZE).toBeLessThanOrEqual(CARD_Y + CARD_H - SHEET_EDGE);
  });

  it("keeps the text column clear of the well and inside the face", () => {
    expect(CONTENT_X).toBeGreaterThan(PORTRAIT_X + PORTRAIT_SIZE);
    expect(CONTENT_X + CONTENT_W).toBeLessThanOrEqual(CARD_X + CARD_W - SHEET_EDGE);
  });

  it("keeps the palette on the strip, clear of the card", () => {
    expect(BTN_X0).toBeGreaterThan(CARD_X + CARD_W);
  });
});

describe("paletteButtonRect / paletteButtonAt", () => {
  it("lays buttons out from BTN_X0 and hit-tests them round-trip", () => {
    const inf = unit("infantry");
    paletteButtons(inf).forEach((b, i) => {
      const r = paletteButtonRect(i);
      expect(r.x).toBeGreaterThanOrEqual(BTN_X0);
      expect(
        paletteButtonAt(r.x + r.width / 2, r.y + r.height / 2, inf),
      ).toBe(b.action);
    });
  });

  it("does not hit buttons over the card or in gaps", () => {
    const inf = unit("infantry");
    const midY = paletteButtonRect(0).y + BTN_H / 2;
    // Over the card (left of the palette).
    expect(paletteButtonAt(PORTRAIT_X + 10, midY, inf)).toBeNull();
    expect(paletteButtonAt(CONTENT_X + 10, midY, inf)).toBeNull();
    // In the gap between the first two buttons.
    expect(paletteButtonAt(BTN_X0 + BTN_W + 2, midY, inf)).toBeNull();
    // Above the button row.
    expect(paletteButtonAt(BTN_X0 + 10, midY - BTN_H, inf)).toBeNull();
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
