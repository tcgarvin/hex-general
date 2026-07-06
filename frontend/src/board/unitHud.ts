// Per-unit HUD anchor scheme + zoom LOD (Phase 8 "Unit facing & HUD slots",
// slice U2). Every piece of chrome around a unit sprite renders at a fixed
// slot, in world pixels relative to the unit's hex centre (+y is down), so
// nothing collides and future additions drop into a reserved slot without a
// re-layout:
//
//   - health:      pip bar centred ABOVE the sprite (top of the hex interior)
//   - order badge: rest/hold text centred BELOW the sprite
//   - status:      icon stack at the hex's top-right corner — reserved, empty
//                  in v0.1 (no statuses exist yet)
//   - selection:   the hex-outline ring (drawn by the planning layer), on the
//                  hex border itself, clear of all interior slots
//
// Rulings settled at slice start:
//   - The health bar shows only when the unit is damaged (hp < max), so a
//     healthy army stays visually quiet. Visible enemies follow the same rule.
//   - Below HUD_LOD_ZOOM the health/status/badge chrome hides and a unit
//     collapses to just its sprite. The selection ring and staged-order
//     geometry are exempt: they scale with the hex and losing them at low
//     zoom would cost more than the noise they add.

import type { Graphics } from "pixi.js";

/** SPEC §5.2: every unit type has 10 max HP, so the pip bar always has 10 slots. */
export const MAX_HP = 10;

/**
 * Zoom below which the per-unit HUD hides. At 0.5 the 36-world-px pip bar is
 * 18 screen px — about the smallest size at which pips still read as a
 * quantity rather than a coloured smear; anything below is noise.
 */
export const HUD_LOD_ZOOM = 0.5;

/** Whether the per-unit HUD (health/status/badge) draws at this zoom. */
export function hudVisibleAtZoom(zoom: number): boolean {
  return zoom >= HUD_LOD_ZOOM;
}

/** The health bar shows only on damaged units; a full-strength army stays quiet. */
export function showsHealth(hp: number): boolean {
  return hp < MAX_HP;
}

// --- slot geometry (world px, hex-centre origin) ---
// Flat-top hex interior: x ∈ ±HEX_SIZE (32), y ∈ ±(√3/2)·HEX_SIZE (≈27.7).
// The sprite bbox is ≤~45 px square (≈ ±22), so the slots hug the hex's top
// and bottom edges just clear of it.

/** Health slot: pip-bar chip centred above the sprite. */
export const PIP_BAR_WIDTH = 36;
export const PIP_BAR_HEIGHT = 5;
export const PIP_BAR_Y = -23;

/** Order-badge slot: staged rest/hold text centred below the sprite. */
export const ORDER_BADGE_Y = 23;

/**
 * Status slot (reserved): future status icons (veteran, exhaustion, …) stack
 * downward from the hex's top-right corner, clear of the health slot. Nothing
 * renders here in v0.1 — the anchors exist so icons drop in without moving
 * any other slot.
 */
export const STATUS_STACK_X = 20;
export const STATUS_STACK_TOP_Y = -10;
export const STATUS_ICON_SIZE = 10;
export const STATUS_ICON_GAP = 2;

/** Centre y of the `index`-th status icon (0 = topmost). */
export function statusIconY(index: number): number {
  return STATUS_STACK_TOP_Y + index * (STATUS_ICON_SIZE + STATUS_ICON_GAP);
}

/**
 * Draw the 10-slot HP bar: a dark backing chip with `hp` segments filled in
 * the owner's colour. Lost HP reads as empty backing, like the concept art's
 * pip rows.
 */
export function drawPipBar(g: Graphics, hp: number, color: number): void {
  g.clear();
  const left = -PIP_BAR_WIDTH / 2;
  const top = PIP_BAR_Y - PIP_BAR_HEIGHT / 2;
  g.roundRect(left, top, PIP_BAR_WIDTH, PIP_BAR_HEIGHT, 1.5);
  g.fill({ color: 0x14140f, alpha: 0.85 });
  const pad = 1;
  const gap = 1;
  const pipWidth = (PIP_BAR_WIDTH - 2 * pad - (MAX_HP - 1) * gap) / MAX_HP;
  for (let i = 0; i < hp; i++) {
    g.rect(left + pad + i * (pipWidth + gap), top + pad, pipWidth, PIP_BAR_HEIGHT - 2 * pad);
  }
  g.fill({ color });
}
