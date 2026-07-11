// Windowed per-beat replay animation timelines. The goal (UI-SPEC §13, Phase 11
// replay polish): a player watching a unit on the map should read the whole
// story without the event log — armies visibly clash, damage counts float off
// units, barrages arc and burst, refused moves bounce off their blocked edge.
//
// `buildAnimTimeline` turns one beat's events into declarative tracks, each
// active over a normalized [t0, t1] window of the beat's animation. The replay
// rAF loop samples the timeline at its current progress via `sampleTimeline`,
// producing the `ReplayAnimOverrides` frame the Board renders. Everything here
// is pure and unit-testable; Pixi drawing stays in Board.tsx.

import { axialToPixel, HEX_SIZE, type Axial } from "@/board/hex";
import { playerColor } from "@/board/palette";
import type { components } from "@/lib/api/schema";
import type { ReplayEvent } from "@/lib/api/types";
import { DEFAULT_FACING, facingAfterStep } from "@/planning/facing";

import type { Beat } from "./beats";

type Unit = components["schemas"]["Unit"];
type Facing = components["schemas"]["Facing"];

// --- sampled frame (what the Board renders each animation frame) ---

/** A rising damage/heal number over a unit. */
export interface FxFloater {
  x: number;
  y: number;
  text: string;
  kind: "damage" | "heal";
  alpha: number;
}

/** A barrage shell in flight, with a short trail behind it. */
export interface FxShell {
  x: number;
  y: number;
  trailX: number;
  trailY: number;
}

/** An impact flash: expanding filled disc + ring. */
export interface FxBurst {
  x: number;
  y: number;
  radius: number;
  alpha: number;
}

/** A ✕ at the edge a unit was refused across (blocked move / traffic jam). */
export interface FxMark {
  x: number;
  y: number;
  color: number;
  alpha: number;
}

/** An expanding team-colour ring on a captured settlement. */
export interface FxRing {
  x: number;
  y: number;
  radius: number;
  color: number;
  alpha: number;
}

export interface ReplayFxFrame {
  floaters: FxFloater[];
  shells: FxShell[];
  bursts: FxBurst[];
  marks: FxMark[];
  rings: FxRing[];
}

/**
 * Per-frame animation overrides for the Board while a beat is animating.
 * The board renders the pre-beat state; overrides are drawn on top:
 *   - `slideOverrides`: pixel positions for units mid-slide/lunge, plus the
 *     facing of the animated step
 *   - `fadingOut`: alpha for units destroyed this beat (still in `state.units`)
 *   - `fadingIn`: alpha for units recruited this beat (not in `state.units`)
 *   - `fx`: transient effect geometry (floaters, shells, bursts, marks, rings)
 */
export interface ReplayAnimOverrides {
  slideOverrides: ReadonlyMap<number, { x: number; y: number; facing: Facing }>;
  fadingOut: ReadonlyMap<number, number>;
  fadingIn: ReadonlyMap<number, { unit: Unit; alpha: number }>;
  fx: ReplayFxFrame;
}

// --- timeline (declarative tracks, built once per beat) ---

/** Normalized active window within the beat's animation, 0 ≤ t0 < t1 ≤ 1. */
interface Window {
  t0: number;
  t1: number;
}

interface Px {
  x: number;
  y: number;
}

/** One-way eased move; holds at `toPx` after the window (the board state is
 * still pre-beat until the whole animation completes). */
interface SlideTrack {
  unitId: number;
  fromPx: Px;
  toPx: Px;
  facing: Facing;
  win: Window;
}

/** Out-and-back motion (attack lunge, blocked-move bounce, jam nudge): the
 * unit travels from `fromPx` to `toPx` and returns within the window. */
interface LungeTrack {
  unitId: number;
  fromPx: Px;
  toPx: Px;
  facing: Facing;
  win: Window;
}

interface FadeOutTrack {
  unitId: number;
  win: Window;
}

interface FadeInTrack {
  unit: Unit;
  win: Window;
}

interface FloaterTrack {
  px: Px;
  text: string;
  kind: "damage" | "heal";
  win: Window;
}

interface ShellTrack {
  fromPx: Px;
  toPx: Px;
  win: Window;
}

interface BurstTrack {
  px: Px;
  win: Window;
}

interface MarkTrack {
  px: Px;
  color: number;
  win: Window;
}

interface RingTrack {
  px: Px;
  color: number;
  win: Window;
}

export interface AnimTimeline {
  slides: SlideTrack[];
  lunges: LungeTrack[];
  fadeOuts: FadeOutTrack[];
  fadeIns: FadeInTrack[];
  floaters: FloaterTrack[];
  shells: ShellTrack[];
  bursts: BurstTrack[];
  marks: MarkTrack[];
  rings: RingTrack[];
}

// --- tuning constants ---

/** Base animation duration (ms at 1× speed) per beat content. */
const DURATION_PULSE_PLAIN = 500;
const DURATION_PULSE_BLOCKED = 800;
const DURATION_PULSE_COMBAT = 1300;
const DURATION_BARRAGE = 1100;
const DURATION_CAPTURE = 700;
const DURATION_REPAIR = 800;
const DURATION_RECRUIT = 500;

/** How far a combat lunge carries toward the contested hex. */
const LUNGE_FRACTION = 0.45;
/** How far a blocked move bounces toward its refused target. */
const BUMP_FRACTION = 0.3;
/** How far a jammed unit nudges toward the congested hex. */
const JAM_FRACTION = 0.18;
/** World-px rise of a damage/heal floater over its window. */
const FLOATER_RISE = HEX_SIZE * 0.9;
/** Horizontal stagger between floaters sharing a hex (multiple barrage hits). */
const FLOATER_STAGGER_X = 14;
/** Peak height of a shell's parabolic arc, in world px. */
const SHELL_ARC_HEIGHT = HEX_SIZE * 0.9;
/** Length of the trail behind a shell, in world px. */
const SHELL_TRAIL_LEN = 14;

const MARK_BLOCKED_COLOR = 0xff5252; // matches PATH_BLOCKED_COLOR
const MARK_JAM_COLOR = 0xffa726; // matches TRAFFIC_COLOR

const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

const px = (c: Axial): Px => axialToPixel(c.q, c.r, HEX_SIZE);

const lerp = (a: Px, b: Px, t: number): Px => ({
  x: a.x + (b.x - a.x) * t,
  y: a.y + (b.y - a.y) * t,
});

// --- duration ---

/**
 * How long this beat's animation runs, in ms. 0 means "snap" (nothing to
 * animate). Also drives the auto-play beat schedule: the playback timer waits
 * for the animation plus a dwell before advancing.
 */
export function beatAnimDurationMs(beat: Beat | undefined, speed: 1 | 2): number {
  if (beat === undefined) return 0;
  const base = beatBaseDurationMs(beat);
  return speed === 2 ? base / 2 : base;
}

function beatBaseDurationMs(beat: Beat): number {
  const has = (kind: ReplayEvent["kind"]): boolean =>
    beat.events.some((e) => e.kind === kind);
  switch (beat.phase) {
    case "pulse":
      if (has("combat_resolved")) return DURATION_PULSE_COMBAT;
      if (has("move_blocked") || has("traffic_jam")) return DURATION_PULSE_BLOCKED;
      return has("unit_moved") ? DURATION_PULSE_PLAIN : 0;
    case "barrage":
      return has("barrage_resolved") ? DURATION_BARRAGE : 0;
    case "capture":
      return DURATION_CAPTURE;
    case "repair":
      return DURATION_REPAIR;
    case "recruitment":
      return has("unit_recruited") ? DURATION_RECRUIT : 0;
    case "economy":
      return 0;
  }
}

// --- build ---

/**
 * Build the animation timeline for one beat. `preBeatUnitsById` /
 * `postBeatUnitsById` are the board states at the beat's boundaries (units may
 * be missing from either: fog redaction, or destroyed/recruited this beat).
 * Returns `null` when there is nothing to animate — the caller snaps.
 */
export function buildAnimTimeline(
  beat: Beat,
  preBeatUnitsById: ReadonlyMap<number, Unit>,
  postBeatUnitsById: ReadonlyMap<number, Unit>,
): AnimTimeline | null {
  const tl: AnimTimeline = {
    slides: [],
    lunges: [],
    fadeOuts: [],
    fadeIns: [],
    floaters: [],
    shells: [],
    bursts: [],
    marks: [],
    rings: [],
  };

  switch (beat.phase) {
    case "pulse":
      buildPulse(tl, beat.events, preBeatUnitsById);
      break;
    case "barrage":
      buildBarrage(tl, beat.events, preBeatUnitsById);
      break;
    case "capture":
      for (const e of beat.events) {
        if (e.kind !== "settlement_captured") continue;
        tl.rings.push({
          px: px(e.coord),
          color: playerColor(e.captured_by),
          win: { t0: 0, t1: 0.7 },
        });
      }
      break;
    case "repair":
      for (const e of beat.events) {
        if (e.kind !== "unit_repaired") continue;
        const healed = e.hp_after - e.hp_before;
        const u = preBeatUnitsById.get(e.unit_id);
        if (healed <= 0 || u === undefined) continue;
        tl.floaters.push({
          px: px(u.coord),
          text: `+${healed}`,
          kind: "heal",
          win: { t0: 0, t1: 0.9 },
        });
      }
      break;
    case "recruitment":
      for (const e of beat.events) {
        if (e.kind !== "unit_recruited") continue;
        const unit = postBeatUnitsById.get(e.unit_id);
        if (unit !== undefined) tl.fadeIns.push({ unit, win: { t0: 0, t1: 1 } });
      }
      break;
    case "economy":
      break; // bank changes are log/roster-only; nothing to show on the map
  }

  staggerSharedFloaters(tl.floaters);

  const empty =
    tl.slides.length === 0 &&
    tl.lunges.length === 0 &&
    tl.fadeOuts.length === 0 &&
    tl.fadeIns.length === 0 &&
    tl.floaters.length === 0 &&
    tl.shells.length === 0 &&
    tl.bursts.length === 0 &&
    tl.marks.length === 0 &&
    tl.rings.length === 0;
  return empty ? null : tl;
}

/**
 * A movement pulse: completed moves slide; refused moves bounce off their
 * blocked edge with a ✕; jammed units nudge toward the congestion; combat
 * plays after the moves settle — attackers lunge into the contested hex,
 * damage floats off every struck participant, the fallen fade, and the
 * surviving entrant slides in to occupy.
 */
function buildPulse(
  tl: AnimTimeline,
  events: ReplayEvent[],
  preBeatUnitsById: ReadonlyMap<number, Unit>,
): void {
  const hasCombat = events.some((e) => e.kind === "combat_resolved");
  // With combat present, moves compress into the front of the beat and the
  // clash plays in the remainder; without it, moves own the whole window.
  const moveWin: Window = hasCombat ? { t0: 0, t1: 0.38 } : { t0: 0, t1: 1 };
  const bumpWin: Window = hasCombat ? { t0: 0, t1: 0.3 } : { t0: 0, t1: 0.55 };
  const markWin: Window = hasCombat ? { t0: 0.12, t1: 0.9 } : { t0: 0.25, t1: 1 };
  const lungeWin: Window = { t0: 0.42, t1: 0.66 };
  const floatWin: Window = { t0: 0.56, t1: 1 };
  const fadeWin: Window = { t0: 0.56, t1: 0.92 };
  const occupyWin: Window = { t0: 0.68, t1: 0.94 };

  for (const e of events) {
    switch (e.kind) {
      case "unit_moved": {
        // Only the first move per unit (each unit steps once per pulse).
        if (tl.slides.some((s) => s.unitId === e.unit_id)) break;
        const fromPx = px(e.origin);
        const toPx = px(e.dest);
        if (fromPx.x === toPx.x && fromPx.y === toPx.y) break;
        const prior = preBeatUnitsById.get(e.unit_id)?.facing ?? DEFAULT_FACING;
        tl.slides.push({
          unitId: e.unit_id,
          fromPx,
          toPx,
          facing: facingAfterStep(prior, e.origin, e.dest),
          win: moveWin,
        });
        break;
      }

      case "move_blocked": {
        const fromPx = px(e.origin);
        const targetPx = px(e.target);
        const prior = preBeatUnitsById.get(e.unit_id)?.facing ?? DEFAULT_FACING;
        tl.lunges.push({
          unitId: e.unit_id,
          fromPx,
          toPx: lerp(fromPx, targetPx, BUMP_FRACTION),
          facing: facingAfterStep(prior, e.origin, e.target),
          win: bumpWin,
        });
        tl.marks.push({
          px: lerp(fromPx, targetPx, 0.5),
          color: MARK_BLOCKED_COLOR,
          win: markWin,
        });
        break;
      }

      case "traffic_jam": {
        const jamPx = px(e.coord);
        for (const unitId of e.unit_ids) {
          const u = preBeatUnitsById.get(unitId);
          if (u === undefined) continue;
          const fromPx = px(u.coord);
          tl.lunges.push({
            unitId,
            fromPx,
            toPx: lerp(fromPx, jamPx, JAM_FRACTION),
            facing: u.facing,
            win: bumpWin,
          });
          tl.marks.push({
            px: lerp(fromPx, jamPx, 0.5),
            color: MARK_JAM_COLOR,
            win: markWin,
          });
        }
        break;
      }

      case "combat_resolved": {
        const hexPx = px(e.coord);
        for (const p of e.participants) {
          // A participant can be missing from the pre-beat state (fog redaction);
          // it then contributes no motion or floater.
          const u = preBeatUnitsById.get(p.unit_id);
          if (u === undefined) continue;
          const atPx = px(u.coord);
          if (p.role === "attacker" && (atPx.x !== hexPx.x || atPx.y !== hexPx.y)) {
            tl.lunges.push({
              unitId: p.unit_id,
              fromPx: atPx,
              toPx: lerp(atPx, hexPx, LUNGE_FRACTION),
              facing: facingAfterStep(u.facing, u.coord, e.coord),
              win: lungeWin,
            });
          }
          if (p.damage_taken > 0) {
            tl.floaters.push({
              px: atPx,
              text: `-${p.damage_taken}`,
              kind: "damage",
              win: floatWin,
            });
          }
          if (p.destroyed) {
            tl.fadeOuts.push({ unitId: p.unit_id, win: fadeWin });
          }
        }
        // The surviving entrant slides in to occupy the contested hex.
        if (e.entrant_id != null) {
          const entrant = preBeatUnitsById.get(e.entrant_id);
          if (entrant !== undefined) {
            tl.slides.push({
              unitId: e.entrant_id,
              fromPx: px(entrant.coord),
              toPx: hexPx,
              facing: facingAfterStep(entrant.facing, entrant.coord, e.coord),
              win: occupyWin,
            });
          }
        }
        break;
      }

      default:
        break; // order_mooted: the unit is already gone; log-only
    }
  }
}

/**
 * A barrage: each shot arcs a shell from the firing artillery to the target
 * (burst-only when the gun is fogged out of the viewer's state), flashes on
 * impact, then floats the damage / fades the victim.
 */
function buildBarrage(
  tl: AnimTimeline,
  events: ReplayEvent[],
  preBeatUnitsById: ReadonlyMap<number, Unit>,
): void {
  const shellWin: Window = { t0: 0, t1: 0.42 };
  const burstWin: Window = { t0: 0.42, t1: 0.64 };
  const floatWin: Window = { t0: 0.5, t1: 1 };
  const fadeWin: Window = { t0: 0.5, t1: 0.95 };

  for (const e of events) {
    if (e.kind !== "barrage_resolved") continue;
    const targetPx = px(e.target);
    const gun = preBeatUnitsById.get(e.artillery_id);
    if (gun !== undefined) {
      tl.shells.push({ fromPx: px(gun.coord), toPx: targetPx, win: shellWin });
    }
    tl.bursts.push({ px: targetPx, win: burstWin });
    const hit = e.hit;
    if (hit != null) {
      if (hit.damage > 0) {
        tl.floaters.push({
          px: targetPx,
          text: `-${hit.damage}`,
          kind: "damage",
          win: floatWin,
        });
      }
      if (hit.destroyed && preBeatUnitsById.has(hit.unit_id)) {
        tl.fadeOuts.push({ unitId: hit.unit_id, win: fadeWin });
      }
    }
  }
}

/** Spread floaters that share a hex (e.g. two barrages on one target) so the
 * numbers don't overprint. */
function staggerSharedFloaters(floaters: FloaterTrack[]): void {
  const byHex = new Map<string, FloaterTrack[]>();
  for (const f of floaters) {
    const key = `${Math.round(f.px.x)},${Math.round(f.px.y)}`;
    const group = byHex.get(key);
    if (group === undefined) byHex.set(key, [f]);
    else group.push(f);
  }
  for (const group of byHex.values()) {
    if (group.length < 2) continue;
    group.forEach((f, i) => {
      f.px = { x: f.px.x + (i - (group.length - 1) / 2) * FLOATER_STAGGER_X, y: f.px.y };
    });
  }
}

// --- sample ---

/** Progress within `win`: null before it opens, clamped to 1 after it closes. */
function winT(win: Window, t: number): number | null {
  if (t <= win.t0) return null;
  return Math.min(1, (t - win.t0) / (win.t1 - win.t0));
}

/**
 * Sample the timeline at normalized beat progress `t` (0..1), producing the
 * overrides frame the Board renders.
 */
export function sampleTimeline(tl: AnimTimeline, t: number): ReplayAnimOverrides {
  const slideOverrides = new Map<number, { x: number; y: number; facing: Facing }>();

  // Slides hold their end position once done — the board state stays pre-beat
  // until the whole beat animation completes.
  for (const s of tl.slides) {
    const u = winT(s.win, t);
    if (u === null) continue;
    const at = lerp(s.fromPx, s.toPx, easeInOutCubic(u));
    slideOverrides.set(s.unitId, { ...at, facing: s.facing });
  }

  // Lunges (out-and-back) override slides during their window; outside it the
  // unit sits at its natural (pre-beat) position, so no entry is needed.
  for (const l of tl.lunges) {
    const u = winT(l.win, t);
    if (u === null || u >= 1) continue;
    const k = u < 0.5 ? easeInOutCubic(u * 2) : easeInOutCubic((1 - u) * 2);
    const at = lerp(l.fromPx, l.toPx, k);
    slideOverrides.set(l.unitId, { ...at, facing: l.facing });
  }

  const fadingOut = new Map<number, number>();
  for (const f of tl.fadeOuts) {
    const u = winT(f.win, t);
    if (u === null) continue;
    fadingOut.set(f.unitId, 1 - u);
  }

  const fadingIn = new Map<number, { unit: Unit; alpha: number }>();
  for (const f of tl.fadeIns) {
    const u = winT(f.win, t) ?? 0;
    fadingIn.set(f.unit.id, { unit: f.unit, alpha: u });
  }

  const fx: ReplayFxFrame = { floaters: [], shells: [], bursts: [], marks: [], rings: [] };

  for (const f of tl.floaters) {
    const u = winT(f.win, t);
    if (u === null || u >= 1) continue;
    fx.floaters.push({
      x: f.px.x,
      y: f.px.y - FLOATER_RISE * u,
      text: f.text,
      kind: f.kind,
      alpha: u < 0.6 ? 1 : 1 - (u - 0.6) / 0.4,
    });
  }

  for (const s of tl.shells) {
    const u = winT(s.win, t);
    if (u === null || u >= 1) continue;
    const at = lerp(s.fromPx, s.toPx, u);
    const lift = Math.sin(Math.PI * u) * SHELL_ARC_HEIGHT;
    // Trail points back along the (straight-line) flight path.
    const dx = s.toPx.x - s.fromPx.x;
    const dy = s.toPx.y - s.fromPx.y;
    const len = Math.hypot(dx, dy) || 1;
    fx.shells.push({
      x: at.x,
      y: at.y - lift,
      trailX: at.x - (dx / len) * SHELL_TRAIL_LEN,
      trailY: at.y - lift - (dy / len) * SHELL_TRAIL_LEN,
    });
  }

  for (const b of tl.bursts) {
    const u = winT(b.win, t);
    if (u === null || u >= 1) continue;
    fx.bursts.push({
      x: b.px.x,
      y: b.px.y,
      radius: HEX_SIZE * (0.25 + 0.75 * u),
      alpha: 0.9 * (1 - u),
    });
  }

  for (const m of tl.marks) {
    const u = winT(m.win, t);
    if (u === null || u >= 1) continue;
    const alpha = u < 0.2 ? u / 0.2 : u > 0.7 ? (1 - u) / 0.3 : 1;
    fx.marks.push({ x: m.px.x, y: m.px.y, color: m.color, alpha });
  }

  for (const r of tl.rings) {
    const u = winT(r.win, t);
    if (u === null || u >= 1) continue;
    fx.rings.push({
      x: r.px.x,
      y: r.px.y,
      radius: HEX_SIZE * (0.5 + 0.6 * u),
      color: r.color,
      alpha: 0.85 * (1 - u),
    });
  }

  return { slideOverrides, fadingOut, fadingIn, fx };
}
