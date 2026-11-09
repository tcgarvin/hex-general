// Owner colours for a world with up to ~200 players.
//
// The viewer is always the same strong blue, the neutral garrisons a dark slate,
// and every other player gets a stable hue hashed from their id, kept out of the
// viewer's blue band so "mine" never looks like "theirs".

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

/**
 * The colour for `owner` as seen by `viewer`. `null` is an unowned site; the
 * neutral garrisons are their own colour.
 */
export function ownerColor(owner: string | null, viewer: string): number {
  if (owner === null) return VACANT;
  if (owner === viewer) return VIEWER;
  if (owner === NEUTRAL_OWNER) return NEUTRAL;
  // Hues 0–360 minus the 190–250 band around the viewer's blue.
  const span = 360 - 60;
  let hue = hash(owner) % span;
  if (hue >= 190) hue += 60;
  const lightness = 0.45 + ((hash(`${owner}:l`) % 3) - 1) * 0.07;
  return hslToRgb(hue, 0.62, lightness);
}
