import { describe, expect, it } from "vitest";

import { NEUTRAL_OWNER, ownerColor } from "./colors";

const VIEWER = "s005";
const rgb = (c: number) => [(c >> 16) & 255, (c >> 8) & 255, c & 255];
/** Rough perceptual distance ("redmean"), enough to catch near-twins. */
function distance(a: number, b: number): number {
  const [r1, g1, b1] = rgb(a);
  const [r2, g2, b2] = rgb(b);
  const r = (r1 + r2) / 2;
  return Math.sqrt((2 + r / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2 + (2 + (255 - r) / 256) * (b1 - b2) ** 2);
}
const slots = (n: number) => Array.from({ length: n }, (_, i) => `s${String(i + 1).padStart(3, "0")}`);

describe("ownerColor", () => {
  it("keeps the viewer's blue, the neutral slate and the vacant site", () => {
    expect(ownerColor(VIEWER, VIEWER)).toBe(0x3d7eff);
    expect(ownerColor(NEUTRAL_OWNER, VIEWER)).toBe(0x4a4f57);
    expect(ownerColor(null, VIEWER)).toBe(0xb8b2a2);
  });

  it("is stable for an owner", () => {
    expect(ownerColor("s042", VIEWER)).toBe(ownerColor("s042", "s001"));
  });

  it("never comes close to the viewer's blue", () => {
    const blue = ownerColor(VIEWER, VIEWER);
    for (const id of slots(200).filter((s) => s !== VIEWER)) expect(distance(ownerColor(id, VIEWER), blue)).toBeGreaterThan(140);
  });

  // The old hashed hues gave 12 players a closest pair at 22 and 100 players exact twins.
  it.each([
    [12, 50],
    [50, 45],
    [200, 8],
  ])("spreads %i players apart (closest pair over %i)", (n, floor) => {
    const colours = slots(n).map((s) => ownerColor(s, "s999"));
    let closest = Infinity;
    for (let i = 0; i < colours.length; i++) {
      for (let j = i + 1; j < colours.length; j++) closest = Math.min(closest, distance(colours[i], colours[j]));
    }
    expect(closest).toBeGreaterThan(floor);
  });
});
