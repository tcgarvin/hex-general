import { describe, expect, it } from "vitest";

import { HEX_SIZE } from "./hex";
import {
  HUD_LOD_ZOOM,
  hudVisibleAtZoom,
  MAX_HP,
  ORDER_BADGE_Y,
  PIP_BAR_HEIGHT,
  PIP_BAR_WIDTH,
  PIP_BAR_Y,
  showsHealth,
  STATUS_ICON_SIZE,
  STATUS_STACK_X,
  statusIconY,
} from "./unitHud";

describe("hudVisibleAtZoom", () => {
  it("hides the HUD below the LOD threshold and shows it at/above", () => {
    expect(hudVisibleAtZoom(HUD_LOD_ZOOM)).toBe(true);
    expect(hudVisibleAtZoom(HUD_LOD_ZOOM + 0.01)).toBe(true);
    expect(hudVisibleAtZoom(HUD_LOD_ZOOM - 0.01)).toBe(false);
    expect(hudVisibleAtZoom(0.125)).toBe(false); // MIN_ZOOM
    expect(hudVisibleAtZoom(2)).toBe(true); // MAX_ZOOM
  });
});

describe("showsHealth", () => {
  it("hides the bar on full-strength units and shows it once damaged", () => {
    expect(showsHealth(MAX_HP)).toBe(false);
    expect(showsHealth(MAX_HP - 1)).toBe(true);
    expect(showsHealth(1)).toBe(true);
  });
});

describe("HUD slot layout", () => {
  // Axis-aligned slot boxes {top, bottom, left, right} in world px.
  const healthSlot = {
    left: -PIP_BAR_WIDTH / 2,
    right: PIP_BAR_WIDTH / 2,
    top: PIP_BAR_Y - PIP_BAR_HEIGHT / 2,
    bottom: PIP_BAR_Y + PIP_BAR_HEIGHT / 2,
  };
  const statusSlot = (index: number) => ({
    left: STATUS_STACK_X - STATUS_ICON_SIZE / 2,
    right: STATUS_STACK_X + STATUS_ICON_SIZE / 2,
    top: statusIconY(index) - STATUS_ICON_SIZE / 2,
    bottom: statusIconY(index) + STATUS_ICON_SIZE / 2,
  });
  type Box = { top: number; bottom: number; left: number; right: number };
  const overlaps = (a: Box, b: Box) =>
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

  it("keeps health above and the order badge below the sprite centre", () => {
    expect(healthSlot.bottom).toBeLessThan(0);
    expect(ORDER_BADGE_Y).toBeGreaterThan(0);
  });

  it("keeps the first few status icons clear of the health and badge slots", () => {
    for (let i = 0; i < 3; i++) {
      expect(overlaps(statusSlot(i), healthSlot)).toBe(false);
    }
    // The stack grows downward, away from the health slot.
    expect(statusIconY(1)).toBeGreaterThan(statusIconY(0));
  });

  it("keeps interior slots inside the hex column (clear of the selection ring's corners)", () => {
    // Flat-top hex: interior half-height at the centre column is (√3/2)·HEX_SIZE.
    const halfHeight = (Math.sqrt(3) / 2) * HEX_SIZE;
    expect(Math.abs(PIP_BAR_Y)).toBeLessThan(halfHeight);
    expect(Math.abs(ORDER_BADGE_Y)).toBeLessThan(halfHeight);
    expect(healthSlot.right).toBeLessThan(HEX_SIZE);
    expect(statusSlot(0).right).toBeLessThan(HEX_SIZE);
  });
});
