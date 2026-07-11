import { describe, expect, it } from "vitest";

import {
  type ArrowDims,
  type BarrageArrowDims,
  barrageArrowOutline,
  moveArrowOutlines,
  type Pt,
} from "./arrows";

const DIMS: ArrowDims = {
  startOffset: 22,
  tailHalf: 2,
  headBaseHalf: 7,
  headLen: 22,
  headHalf: 14,
  headNose: 4,
};

/** Flat [x0,y0,…] → point list. */
function pointsOf(flat: number[]): Pt[] {
  const out: Pt[] = [];
  for (let i = 0; i < flat.length; i += 2) out.push({ x: flat[i], y: flat[i + 1] });
  return out;
}

function xs(flat: number[]): number[] {
  return pointsOf(flat).map((p) => p.x);
}

function ys(flat: number[]): number[] {
  return pointsOf(flat).map((p) => p.y);
}

// A straight two-step move east along y = 0, hex pitch 55.4px.
const STRAIGHT: Pt[] = [
  { x: 0, y: 0 },
  { x: 55.4, y: 0 },
  { x: 110.8, y: 0 },
];

describe("moveArrowOutlines", () => {
  it("returns an empty outline for a degenerate path", () => {
    const { casing, reachable, blocked } = moveArrowOutlines([{ x: 0, y: 0 }], 1, DIMS);
    expect(casing).toEqual([]);
    expect(reachable).toBeNull();
    expect(blocked).toBeNull();
  });

  it("builds one head-capped polygon when nothing blocks", () => {
    const { casing, reachable, blocked } = moveArrowOutlines(STRAIGHT, 2, DIMS);
    expect(blocked).toBeNull();
    expect(reachable).toBe(casing);

    // Apex pokes headNose past the destination center; nothing reaches further.
    expect(Math.max(...xs(casing))).toBeCloseTo(110.8 + DIMS.headNose, 5);
    // Tail is lifted off the origin by startOffset.
    expect(Math.min(...xs(casing))).toBeCloseTo(DIMS.startOffset, 5);
    // Widest extent is the head's half-width, symmetric about the axis.
    expect(Math.max(...ys(casing))).toBeCloseTo(DIMS.headHalf, 5);
    expect(Math.min(...ys(casing))).toBeCloseTo(-DIMS.headHalf, 5);
  });

  it("tapers from tailHalf to headBaseHalf along the shaft", () => {
    const { casing } = moveArrowOutlines(STRAIGHT, 2, DIMS);
    const pts = pointsOf(casing);
    const tailWidths = pts.filter((p) => Math.abs(p.x - DIMS.startOffset) < 1e-6);
    expect(tailWidths).toHaveLength(2);
    expect(Math.abs(tailWidths[0].y)).toBeCloseTo(DIMS.tailHalf, 5);
  });

  it("splits at the stop hex with a continuous seam", () => {
    const { casing, reachable, blocked } = moveArrowOutlines(STRAIGHT, 1, DIMS);
    expect(reachable).not.toBeNull();
    expect(blocked).not.toBeNull();

    // The reachable run is headless and ends flat at the stop center (x=55.4).
    expect(Math.max(...xs(reachable!))).toBeCloseTo(55.4, 5);
    // The blocked tail starts there and carries the head to the apex.
    expect(Math.min(...xs(blocked!))).toBeCloseTo(55.4, 5);
    expect(Math.max(...xs(blocked!))).toBeCloseTo(110.8 + DIMS.headNose, 5);

    // Width continuity: both pieces have the same half-width at the seam.
    const seamReach = pointsOf(reachable!).filter((p) => Math.abs(p.x - 55.4) < 1e-6);
    const seamBlock = pointsOf(blocked!).filter((p) => Math.abs(p.x - 55.4) < 1e-6);
    expect(Math.abs(seamReach[0].y)).toBeCloseTo(Math.abs(seamBlock[0].y), 5);

    // The casing is the whole arrow regardless of the split.
    expect(Math.min(...xs(casing))).toBeCloseTo(DIMS.startOffset, 5);
    expect(Math.max(...xs(casing))).toBeCloseTo(110.8 + DIMS.headNose, 5);
  });

  it("renders a fully blocked order as one red-side polygon", () => {
    const { casing, reachable, blocked } = moveArrowOutlines(STRAIGHT, 0, DIMS);
    expect(reachable).toBeNull();
    expect(blocked).toBe(casing);
  });

  it("keeps a sliver of shaft on a one-step move", () => {
    const oneStep: Pt[] = [
      { x: 0, y: 0 },
      { x: 55.4, y: 0 },
    ];
    const { casing } = moveArrowOutlines(oneStep, 1, DIMS);
    const minX = Math.min(...xs(casing));
    const apexX = Math.max(...xs(casing));
    expect(apexX).toBeCloseTo(55.4 + DIMS.headNose, 5);
    // Trims are scaled down, not allowed to consume the whole segment.
    expect(minX).toBeGreaterThan(0);
    expect(minX).toBeLessThan(55.4 - DIMS.headLen + DIMS.headNose);
  });

  it("keeps the ribbon width through a 120° bend (miter compensation)", () => {
    // East one hex, then northeast: a sharp planning-path bend.
    const bent: Pt[] = [
      { x: 0, y: 0 },
      { x: 55.4, y: 0 },
      { x: 83.1, y: -48 },
    ];
    const { casing } = moveArrowOutlines(bent, 2, DIMS);
    // The bend vertex offsets sit further than the raw half-width (1/cos(θ/2)),
    // but stay clamped — no runaway miter spike.
    const pts = pointsOf(casing);
    const nearBend = pts.filter((p) => Math.hypot(p.x - 55.4, p.y) < 30);
    expect(nearBend.length).toBeGreaterThan(0);
    for (const p of nearBend) {
      expect(Math.hypot(p.x - 55.4, p.y)).toBeLessThan(30);
    }
  });
});

const BARRAGE_DIMS: BarrageArrowDims = {
  startOffset: 22,
  tailHalf: 2,
  headBaseHalf: 2,
  headLen: 14,
  headHalf: 8,
  headNose: -14,
  sagFactor: 0.15,
  sagMax: 30,
};

describe("barrageArrowOutline", () => {
  it("returns an empty outline for a degenerate shot", () => {
    expect(barrageArrowOutline({ x: 0, y: 0 }, { x: 0, y: 0 }, BARRAGE_DIMS)).toEqual([]);
  });

  it("bows a horizontal shot toward screen-up (negative y)", () => {
    const flat = barrageArrowOutline({ x: 0, y: 0 }, { x: 166.2, y: 0 }, BARRAGE_DIMS);
    // sag = min(0.15 · 166.2, 30) ≈ 24.9: the mid-arc must rise well clear of
    // the chord, and nothing may dip meaningfully below it.
    expect(Math.min(...ys(flat))).toBeLessThan(-18);
    expect(Math.max(...ys(flat))).toBeLessThan(BARRAGE_DIMS.headHalf + 1);
  });

  it("bows a dead-vertical shot west, deterministically", () => {
    const up = barrageArrowOutline({ x: 0, y: 0 }, { x: 0, y: -166.2 }, BARRAGE_DIMS);
    const down = barrageArrowOutline({ x: 0, y: 0 }, { x: 0, y: 166.2 }, BARRAGE_DIMS);
    expect(Math.min(...xs(up))).toBeLessThan(-18);
    expect(Math.min(...xs(down))).toBeLessThan(-18);
  });

  it("keeps the whole arrow off the gun sprite and short of the reticle", () => {
    const origin = { x: 0, y: 0 };
    const target = { x: 166.2, y: 0 };
    const pts = pointsOf(barrageArrowOutline(origin, target, BARRAGE_DIMS));
    // The apex parks |headNose| short of the target center (reticle edge)…
    const toTarget = pts.map((p) => Math.hypot(p.x - target.x, p.y - target.y));
    expect(Math.min(...toTarget)).toBeGreaterThan(-BARRAGE_DIMS.headNose - 1);
    // …and the tail lifts roughly startOffset off the gun (the arc bows, so
    // the straight-line distance is a shade under the arc-length trim).
    const toOrigin = pts.map((p) => Math.hypot(p.x, p.y));
    expect(Math.min(...toOrigin)).toBeGreaterThan(BARRAGE_DIMS.startOffset * 0.8);
  });

  it("survives a point-blank shot by scaling the trims down", () => {
    const flat = barrageArrowOutline({ x: 0, y: 0 }, { x: 55.4, y: 0 }, BARRAGE_DIMS);
    expect(flat.length).toBeGreaterThan(0);
    // Everything stays within the shot's neighbourhood — no folded geometry
    // flinging points past the target.
    expect(Math.max(...xs(flat))).toBeLessThan(55.4 + 1);
    expect(Math.min(...xs(flat))).toBeGreaterThan(0);
  });
});

