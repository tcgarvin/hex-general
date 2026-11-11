// Dashed polylines for Pixi Graphics, which has no dash style of its own.

export interface Point {
  x: number;
  y: number;
}

/**
 * The dash segments `[x0, y0, x1, y1]` along the polyline `points`, `dash` long
 * with `gap` between them. The pattern carries across corners, so a bend doesn't
 * restart it and the line keeps an even rhythm.
 */
export function dashSegments(points: readonly Point[], dash: number, gap: number): [number, number, number, number][] {
  if (dash <= 0 || gap < 0) throw new Error(`bad dash pattern ${dash}/${gap}`);
  const out: [number, number, number, number][] = [];
  const period = dash + gap;
  let phase = 0; // distance into the current dash+gap period
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i];
    const b = points[i + 1];
    const len = Math.hypot(b.x - a.x, b.y - a.y);
    if (len === 0) continue;
    const ux = (b.x - a.x) / len;
    const uy = (b.y - a.y) / len;
    let t = 0;
    while (t < len) {
      const inDash = phase < dash;
      const left = (inDash ? dash : period) - phase;
      const step = Math.min(left, len - t);
      if (inDash) out.push([a.x + ux * t, a.y + uy * t, a.x + ux * (t + step), a.y + uy * (t + step)]);
      t += step;
      phase = (phase + step) % period;
    }
  }
  return out;
}
