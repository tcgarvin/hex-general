// Owner colours for a world with up to ~200 players.
//
// The viewer is always the same strong blue, the neutral garrisons a dark slate.
// Every other player's colour comes from their slot number (`s001`…`s200`, the
// fill order) stepped through the R2 low-discrepancy sequence over hue and
// lightness, so any run of slots is spread evenly over the colour space instead
// of landing on hash collisions (a hash gave 100 players many near-twins). Hues
// stay out of a wide band around the viewer's blue so "mine" never looks like
// "theirs". Ids that aren't slot numbers fall back to a hash into the same space.
// Ported by the season viewer and the mock scripts: keep the signature.

const VIEWER = 0x3d7eff;
const NEUTRAL = 0x4a4f57;
const VACANT = 0xb8b2a2;

/** The `neutral` garrison owner id (api/app/engine/models.py `NEUTRAL`). */
export const NEUTRAL_OWNER = "neutral";

function hash(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function hslToRgb(h: number, s: number, l: number): number {
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const to = (x: number) => Math.round(x * 255);
  return (to(f(0)) << 16) | (to(f(8)) << 8) | to(f(4));
}

/** Hues kept clear of the viewer's blue (about 220°): the band [185°, 255°). */
const BLUE_BAND_START = 185;
const BLUE_BAND_WIDTH = 70;

// R2 sequence constants (1/g and 1/g² for the plastic number g): consecutive
// terms fill the unit square evenly, whatever the count.
const R2_A = 0.7548776662466927;
const R2_B = 0.5698402909980532;

/** Where `owner` sits in the colour space: two numbers in [0, 1). */
function colourPoint(owner: string): [number, number] {
  const slot = /^s(\d+)$/.exec(owner);
  if (slot !== null) {
    const n = Number(slot[1]);
    return [(0.5 + R2_A * n) % 1, (0.5 + R2_B * n) % 1];
  }
  return [(hash(owner) % 1000) / 1000, (hash(`${owner}:l`) % 1000) / 1000];
}

/**
 * The colour for `owner` as seen by `viewer`. `null` is an unowned site; the
 * neutral garrisons are their own colour.
 */
export function ownerColor(owner: string | null, viewer: string): number {
  if (owner === null) return VACANT;
  if (owner === viewer) return VIEWER;
  if (owner === NEUTRAL_OWNER) return NEUTRAL;
  const [u, v] = colourPoint(owner);
  let hue = u * (360 - BLUE_BAND_WIDTH);
  if (hue >= BLUE_BAND_START) hue += BLUE_BAND_WIDTH;
  // Mid lightness only, so every colour reads on the map and against the badges.
  const lightness = 0.34 + v * 0.3;
  return hslToRgb(hue, 0.72, lightness);
}
