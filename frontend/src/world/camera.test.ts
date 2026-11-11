import { describe, expect, it } from "vitest";

import {
  cameraBetween,
  clampCamera,
  comfortablyVisible,
  fitCamera,
  fitScale,
  followCamera,
  panStep,
  PAN_SPEED,
  zoomAbout,
  type Bounds,
} from "./camera";

const map: Bounds = { minX: -32, minY: -32, maxX: 1968, maxY: 1968 }; // 2000 × 2000
const view = { width: 1000, height: 600 };

describe("clampCamera", () => {
  it("leaves a camera well inside the map alone", () => {
    const cam = { x: -500, y: -400, scale: 1 };
    expect(clampCamera(cam, map, view)).toEqual(cam);
  });

  it("stops the map leaving more than 30% of the viewport empty on any side", () => {
    // Dragged far right: the map's left edge would be at +900 on screen.
    const right = clampCamera({ x: 932, y: 0, scale: 1 }, map, view);
    expect(right.x + map.minX * 1).toBeCloseTo(300);
    // Dragged far left: the map's right edge would be off the left of the screen.
    const left = clampCamera({ x: -5000, y: 0, scale: 1 }, map, view);
    expect(left.x + map.maxX * 1).toBeCloseTo(700);
    const down = clampCamera({ x: 0, y: 5000, scale: 1 }, map, view);
    expect(down.y + map.minY).toBeCloseTo(180);
    const up = clampCamera({ x: 0, y: -5000, scale: 1 }, map, view);
    expect(up.y + map.maxY).toBeCloseTo(420);
  });

  it("centres an axis on which the map is smaller than the viewport", () => {
    const scale = 0.2; // map is 400 px square on screen
    const cam = clampCamera({ x: 999, y: -999, scale }, map, view);
    expect(cam.x + map.minX * scale).toBeCloseTo((1000 - 400) / 2);
    expect(cam.y + map.minY * scale).toBeCloseTo((600 - 400) / 2);
  });

  it("centres only the short axis when the map overflows the other", () => {
    const scale = 0.4; // 800 px: narrower than 1000, taller than 600
    const cam = clampCamera({ x: 0, y: 0, scale }, map, view);
    expect(cam.x + map.minX * scale).toBeCloseTo(100);
    expect(cam.y).toBe(0); // the top edge at +12.8 is inside the margin
  });
});

describe("fit and zoom", () => {
  it("fits the whole map, centred", () => {
    const s = fitScale(map, view);
    expect(s).toBeCloseTo(0.3);
    const cam = fitCamera(map, view);
    expect(cam.scale).toBeCloseTo(0.3);
    expect(cam.x + map.minX * s).toBeCloseTo(200);
    expect(cam.y + map.minY * s).toBeCloseTo(0);
  });

  it("keeps the world point under the cursor fixed while zooming", () => {
    const cam = { x: -100, y: -50, scale: 1 };
    const next = zoomAbout(cam, 2, 300, 200);
    const before = { x: (300 - cam.x) / cam.scale, y: (200 - cam.y) / cam.scale };
    const after = { x: (300 - next.x) / next.scale, y: (200 - next.y) / next.scale };
    expect(after.x).toBeCloseTo(before.x);
    expect(after.y).toBeCloseTo(before.y);
  });
});

describe("cameraBetween", () => {
  const from = { x: 0, y: 0, scale: 1 };
  const to = { x: -1000, y: -400, scale: 0.25 };

  it("starts at the start and lands exactly on the target", () => {
    expect(cameraBetween(from, to, 0, view)).toEqual({ x: 0, y: 0, scale: 1 });
    expect(cameraBetween(from, to, 1, view)).toEqual(to);
  });

  it("zooms geometrically and glides the view centre", () => {
    const mid = cameraBetween(from, to, 0.5, view);
    expect(mid.scale).toBeCloseTo(0.5);
    const centre = (c: typeof from) => (view.width / 2 - c.x) / c.scale;
    expect(centre(mid)).toBeCloseTo((centre(from) + centre(to)) / 2);
  });
});

describe("panStep", () => {
  it("moves the map opposite to the held direction at the pan speed", () => {
    expect(panStep({ x: 1, y: 0, fast: false }, 0.5)).toEqual({ dx: -PAN_SPEED / 2, dy: -0 });
    expect(panStep({ x: 0, y: -1, fast: true }, 0.5)).toEqual({ dx: -0, dy: PAN_SPEED });
  });

  it("normalises diagonals", () => {
    const { dx, dy } = panStep({ x: 1, y: 1, fast: false }, 1);
    expect(Math.hypot(dx, dy)).toBeCloseTo(PAN_SPEED);
  });

  it("does nothing with no keys held", () => {
    expect(panStep({ x: 0, y: 0, fast: true }, 1)).toEqual({ dx: 0, dy: 0 });
  });
});

describe("comfortablyVisible", () => {
  const insets = { left: 300, right: 330, top: 10, bottom: 80 };

  it("rejects points under a panel or off-screen", () => {
    expect(comfortablyVisible(500, 300, view, insets)).toBe(true);
    expect(comfortablyVisible(100, 300, view, insets)).toBe(false);
    expect(comfortablyVisible(900, 300, view, insets)).toBe(false);
    expect(comfortablyVisible(500, 580, view, insets)).toBe(false);
    expect(comfortablyVisible(-20, 300, view, insets)).toBe(false);
  });

  it("shrinks the insets on a narrow screen so the middle stays usable", () => {
    const narrow = { width: 600, height: 600 };
    expect(comfortablyVisible(300, 300, narrow, insets)).toBe(true);
    expect(comfortablyVisible(150, 300, narrow, insets)).toBe(false);
  });
});

describe("followCamera (turn playback)", () => {
  const view = { width: 1600, height: 900 };
  const insets = { left: 300, right: 300, top: 20, bottom: 80 };
  const box = { minX: 1000, minY: 1000, maxX: 1200, maxY: 1100 };

  it("stays put when the box is already comfortably on screen", () => {
    const cam = { scale: 1, x: 800 - 1100, y: 450 - 1050 };
    expect(followCamera(cam, box, view, insets, 0.4)).toBeNull();
  });

  it("pans to the box keeping the zoom when it fits", () => {
    const cam = { scale: 1, x: 0, y: 0 };
    const to = followCamera(cam, box, view, insets, 0.4)!;
    expect(to.scale).toBe(1);
    // The box centre lands in the middle of the clear area.
    expect(to.x + 1100 * to.scale).toBeCloseTo((300 + 1600 - 300) / 2);
    expect(to.y + 1050 * to.scale).toBeCloseTo((20 + 900 - 80) / 2);
  });

  it("zooms out only as far as needed, not past the tile switch", () => {
    const wide = { minX: 0, minY: 0, maxX: 3000, maxY: 500 };
    const to = followCamera({ scale: 1, x: 0, y: 0 }, wide, view, insets, 0.2)!;
    expect(to.scale).toBeCloseTo(1000 / 3000);
    const tooWide = { minX: 0, minY: 0, maxX: 30000, maxY: 500 };
    expect(followCamera({ scale: 1, x: 0, y: 0 }, tooWide, view, insets, 0.2)!.scale).toBeLessThan(0.2);
  });

  it("zooms in from the overview to the preferred close-up zoom", () => {
    expect(followCamera({ scale: 0.05, x: 0, y: 0 }, box, view, insets, 0.4)!.scale).toBe(0.4);
    expect(followCamera({ scale: 0.05, x: 0, y: 0 }, box, view, insets, 0.4, 0.9)!.scale).toBe(0.9);
  });
});
