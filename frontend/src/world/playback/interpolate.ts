// Turn playback, step 3: what to draw at a moment inside a beat.
//
// `frameAt(playback, k, phase)` samples beat `k` at `phase` in [0, 1]: every
// drawn unit with a fractional axial position (sliding between hexes, lunging
// at a fight, bumping a blocked edge), an alpha (fading in when it comes into
// sight or is recruited, out when it dies or is lost to the fog) and the hp it
// shows at that instant, plus the transient effects in play (combat flash,
// barrage shell and impact, damage numbers, capture ring) with their own 0..1
// progress. Phase 0 draws exactly the beat's start snapshot and phase 1 its end
// snapshot. Positions are axial so the renderer converts with `axialToPixel`
// (linear, so a fractional `q, r` lands on the straight line between hexes).
//
// Timing inside a beat (fractions of the beat's animation):
//   0.00-0.20 units coming into sight fade in
//   0.10-0.85 moves, packs and deploys (a unit's several steps share the slot)
//   0.15-0.50 attackers lunge;  0.25-0.70 combat flash;  0.45 damage lands
//   0.55-0.85 the entrant takes the hex;  dead units fade over 0.55-0.85
//   0.00-0.55 barrage shell flight, 0.50-0.80 impact, 0.60 damage lands
//   0.80-1.00 units dropping out of sight fade out

import { axialToPixel, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { applyAction, type Playback } from "./reconstruct";
import { actionUnitIds, type Beat, type BeatAction, type PlaybackUnit } from "./timeline";

type DeployCause = components["schemas"]["DeployCause"];

/** A fractional axial position. */
export interface AxialPoint {
  q: number;
  r: number;
}

export interface DrawnUnit extends PlaybackUnit {
  /** Where to draw it now (fractional axial); `coord` is the hex it counts as standing on. */
  at: AxialPoint;
  /** 0..1; units at 0 are omitted from a frame. */
  alpha: number;
  /** It is sliding between hexes right now. */
  moving: boolean;
}

export type Effect =
  | { kind: "combat"; eventIndex: number; at: Axial; progress: number; viewerInvolved: boolean }
  | { kind: "shell"; eventIndex: number; from: Axial | null; to: Axial; progress: number }
  | { kind: "impact"; eventIndex: number; at: Axial; progress: number }
  | { kind: "damage"; eventIndex: number; unitId: number; at: AxialPoint; amount: number; progress: number }
  | { kind: "heal"; eventIndex: number; unitId: number; at: AxialPoint; amount: number; progress: number }
  | { kind: "capture"; eventIndex: number; at: Axial; owner: string; previousOwner: string | null; progress: number }
  | { kind: "blocked"; eventIndex: number; from: Axial; toward: Axial; progress: number }
  | { kind: "halt"; eventIndex: number; at: Axial; cause: DeployCause; progress: number }
  | { kind: "recruit"; eventIndex: number; at: Axial; owner: string; progress: number };

export interface Frame {
  beat: number;
  phase: number;
  units: DrawnUnit[];
  effects: Effect[];
}

// --- timing ------------------------------------------------------------------

const FADE_IN: Window = [0, 0.2];
const MOVE_SLOT: Window = [0.1, 0.85];
const MOVE_SLOT_BEFORE_ENTRY: Window = [0.1, 0.5];
const ENTRY: Window = [0.55, 0.85];
const LUNGE: Window = [0.15, 0.5];
const COMBAT_FLASH: Window = [0.25, 0.7];
const COMBAT_HIT = 0.45;
const COMBAT_DEATH: Window = [0.55, 0.85];
const FLOATER: Window = [0.45, 0.95];
const SHELL: Window = [0, 0.55];
const IMPACT: Window = [0.5, 0.8];
const BARRAGE_HIT = 0.6;
const BARRAGE_DEATH: Window = [0.65, 0.95];
const BARRAGE_FLOATER: Window = [0.6, 1];
const BUMP: Window = [0.2, 0.6];
const CAPTURE: Window = [0.1, 0.9];
const RECRUIT: Window = [0.3, 0.7];
const REPAIR_AT = 0.5;
const HEAL_FLOATER: Window = [0.4, 0.9];
const DISBAND: Window = [0.3, 0.8];
const CONCEAL: Window = [0.8, 1];
const UNSEEN_OUT: Window = [0, 0.35];
const UNSEEN_JUMP = 0.4;
const UNSEEN_IN: Window = [0.45, 0.8];
const HALT: Window = [0.2, 0.7];

/** How far toward the target hex a lunge, bump or nudge carries. */
const LUNGE_REACH = 0.4;
const BUMP_REACH = 0.3;
const NUDGE_REACH = 0.18;

type Window = readonly [number, number];

/** Base animation length (ms at 1x) of a beat, by what happens in it. */
export function beatDurationMs(beat: Beat): number {
  const has = (kind: BeatAction["kind"]) => beat.actions.some((a) => a.kind === kind);
  if (beat.phase === "barrage") return 1300;
  if (has("combat")) return 1400;
  if (beat.phase === "pulse") return beat.actions.some((a) => a.kind !== "steady") ? 700 : 400;
  if (beat.phase === "capture") return 900;
  if (beat.phase === "recruit") return 900;
  const visual = beat.actions.some((a) => a.kind !== "steady" && a.kind !== "correct");
  return visual ? 800 : 450;
}

// --- the per-beat plan -------------------------------------------------------

interface Segment {
  t0: number;
  t1: number;
  from: PlaybackUnit;
  to: PlaybackUnit;
}

interface Fade {
  window: Window;
  direction: "in" | "out";
}

interface Lunge {
  window: Window;
  toward: Axial;
  reach: number;
}

interface Track {
  /** The state at phase 0 (for a unit that is not drawn then, the state it appears in). */
  base: PlaybackUnit;
  segments: Segment[];
  fades: Fade[];
  lunges: Lunge[];
  hp: { t: number; hp: number }[];
}

interface TimedEffect {
  window: Window;
  make: (progress: number, at: (unitId: number) => AxialPoint | null) => Effect | null;
}

interface BeatPlan {
  tracks: Map<number, Track>;
  effects: TimedEffect[];
}

const plans = new WeakMap<Playback, BeatPlan[]>();

function planFor(pb: Playback, k: number): BeatPlan {
  let list = plans.get(pb);
  if (list === undefined) {
    list = [];
    plans.set(pb, list);
  }
  list[k] ??= buildPlan(pb.beats[k], pb.snapshots[k], pb.viewer);
  return list[k];
}

/** A change to a unit within the beat, waiting for its time slot. */
interface Change {
  from: PlaybackUnit;
  to: PlaybackUnit;
  entry: boolean;
}

function buildPlan(beat: Beat, start: ReadonlyMap<number, PlaybackUnit>, viewer: string): BeatPlan {
  const tracks = new Map<number, Track>();
  const effects: TimedEffect[] = [];
  const changes = new Map<number, Change[]>();
  const work = new Map(start);
  for (const [id, u] of start) tracks.set(id, { base: u, segments: [], fades: [], lunges: [], hp: [] });

  const track = (u: PlaybackUnit): Track => {
    let t = tracks.get(u.id);
    if (t === undefined) {
      t = { base: u, segments: [], fades: [], lunges: [], hp: [] };
      tracks.set(u.id, t);
    }
    return t;
  };
  const queue = (id: number, from: PlaybackUnit, to: PlaybackUnit, entry = false) => {
    const list = changes.get(id) ?? [];
    list.push({ from, to, entry });
    changes.set(id, list);
  };
  const snapAt = (t: number, from: PlaybackUnit, to: PlaybackUnit) => {
    track(from).segments.push({ t0: t, t1: t, from, to });
  };

  for (const a of beat.actions) {
    const ids = actionUnitIds(a);
    const before = new Map(ids.map((id) => [id, work.get(id)]));
    applyAction(work, a);
    const after = (id: number) => work.get(id);
    switch (a.kind) {
      case "move":
      case "deploy":
      case "pack": {
        const from = before.get(a.unitId);
        const to = after(a.unitId);
        if (from !== undefined && to !== undefined) queue(a.unitId, from, to);
        if (a.kind === "deploy" && (a.cause === "contact" || a.cause === "barraged")) {
          effects.push({ window: HALT, make: (progress) => ({ kind: "halt", eventIndex: a.eventIndex, at: a.to, cause: a.cause, progress }) });
        }
        break;
      }
      case "blocked": {
        const u = before.get(a.unitId);
        if (u !== undefined) track(u).lunges.push({ window: BUMP, toward: a.toward, reach: BUMP_REACH });
        effects.push({
          window: BUMP,
          make: (progress) => ({ kind: "blocked", eventIndex: a.eventIndex, from: a.from, toward: a.toward, progress }),
        });
        break;
      }
      case "jam":
      case "held": {
        const toward = a.kind === "jam" ? a.at : a.toward;
        for (const id of ids) {
          const u = before.get(id);
          if (u !== undefined) track(u).lunges.push({ window: BUMP, toward, reach: NUDGE_REACH });
        }
        break;
      }
      case "stuck":
        break;
      case "combat": {
        for (const s of a.sides) {
          const u = before.get(s.unitId);
          if (u === undefined) continue;
          const t = track(u);
          if (s.role === "attacker" && s.unitId !== a.entrantId) t.lunges.push({ window: LUNGE, toward: a.at, reach: LUNGE_REACH });
          t.hp.push({ t: COMBAT_HIT, hp: s.hpAfter });
          if (s.destroyed) t.fades.push({ window: COMBAT_DEATH, direction: "out" });
          if (s.damageTaken > 0) {
            effects.push({
              window: FLOATER,
              make: (progress, at) => {
                const where = at(s.unitId);
                return where === null
                  ? null
                  : { kind: "damage", eventIndex: a.eventIndex, unitId: s.unitId, at: where, amount: s.damageTaken, progress };
              },
            });
          }
        }
        if (a.entrantId !== null) {
          const from = before.get(a.entrantId);
          const to = after(a.entrantId);
          if (from !== undefined && to !== undefined) queue(a.entrantId, from, to, true);
        }
        const viewerInvolved = a.sides.some((s) => s.owner === viewer);
        effects.push({
          window: COMBAT_FLASH,
          make: (progress) => ({ kind: "combat", eventIndex: a.eventIndex, at: a.at, progress, viewerInvolved }),
        });
        break;
      }
      case "barrage": {
        effects.push({
          window: SHELL,
          make: (progress) => ({ kind: "shell", eventIndex: a.eventIndex, from: a.from, to: a.target, progress }),
        });
        effects.push({ window: IMPACT, make: (progress) => ({ kind: "impact", eventIndex: a.eventIndex, at: a.target, progress }) });
        for (const s of a.strikes) {
          const u = before.get(s.unitId);
          if (u === undefined) continue;
          const t = track(u);
          t.hp.push({ t: BARRAGE_HIT, hp: s.hpAfter });
          if (s.destroyed) t.fades.push({ window: BARRAGE_DEATH, direction: "out" });
          if (s.damage > 0) {
            effects.push({
              window: BARRAGE_FLOATER,
              make: (progress) => ({ kind: "damage", eventIndex: a.eventIndex, unitId: s.unitId, at: u.coord, amount: s.damage, progress }),
            });
          }
        }
        break;
      }
      case "capture":
        effects.push({
          window: CAPTURE,
          make: (progress) => ({
            kind: "capture",
            eventIndex: a.eventIndex,
            at: a.at,
            owner: a.owner,
            previousOwner: a.previousOwner,
            progress,
          }),
        });
        break;
      case "repair": {
        const u = before.get(a.unitId);
        if (u === undefined) break;
        track(u).hp.push({ t: REPAIR_AT, hp: a.hpAfter });
        effects.push({
          window: HEAL_FLOATER,
          make: (progress) => ({ kind: "heal", eventIndex: a.eventIndex, unitId: a.unitId, at: u.coord, amount: a.hpAfter - a.hpBefore, progress }),
        });
        break;
      }
      case "recruit": {
        const u = after(a.unitId)!;
        track(u).fades.push({ window: RECRUIT, direction: "in" });
        effects.push({ window: RECRUIT, make: (progress) => ({ kind: "recruit", eventIndex: a.eventIndex, at: a.at, owner: a.owner, progress }) });
        break;
      }
      case "disband": {
        const u = before.get(a.unitId);
        if (u !== undefined) track(u).fades.push({ window: DISBAND, direction: "out" });
        break;
      }
      case "reveal": {
        track(a.unit).fades.push({ window: FADE_IN, direction: "in" });
        break;
      }
      case "conceal": {
        const u = before.get(a.unitId);
        if (u !== undefined) track(u).fades.push({ window: CONCEAL, direction: "out" });
        break;
      }
      case "relocate": {
        const from = before.get(a.unit.id);
        if (from === undefined) break;
        if (a.unit.owner === viewer) queue(a.unit.id, from, a.unit);
        else {
          const t = track(from);
          t.fades.push({ window: UNSEEN_OUT, direction: "out" }, { window: UNSEEN_IN, direction: "in" });
          snapAt(UNSEEN_JUMP, from, a.unit);
          t.hp.push({ t: UNSEEN_JUMP, hp: a.unit.hp });
        }
        break;
      }
      case "correct":
      case "steady": {
        const id = a.kind === "correct" ? a.unit.id : a.unitId;
        const from = before.get(id);
        const to = after(id);
        if (from !== undefined && to !== undefined) {
          snapAt(1, from, to);
          track(from).hp.push({ t: 1, hp: to.hp });
        }
        break;
      }
    }
  }

  for (const [id, list] of changes) {
    const t = tracks.get(id)!;
    const generic = list.filter((c) => !c.entry);
    const [a, b] = list.some((c) => c.entry) ? MOVE_SLOT_BEFORE_ENTRY : MOVE_SLOT;
    const width = (b - a) / Math.max(1, generic.length);
    let i = 0;
    for (const c of list) {
      const [t0, t1] = c.entry ? ENTRY : [a + i * width, a + (i + 1) * width];
      if (!c.entry) i++;
      t.segments.push({ t0, t1, from: c.from, to: c.to });
    }
  }
  for (const t of tracks.values()) t.segments.sort((x, y) => x.t0 - y.t0);
  return { tracks, effects };
}

// --- sampling ----------------------------------------------------------------

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const progressIn = ([t0, t1]: Window, p: number) => (t1 <= t0 ? (p >= t1 ? 1 : 0) : clamp01((p - t0) / (t1 - t0)));
const ease = (u: number) => (u < 0.5 ? 2 * u * u : 1 - 2 * (1 - u) * (1 - u));
const lerp = (a: Axial | AxialPoint, b: Axial | AxialPoint, u: number): AxialPoint => ({
  q: a.q + (b.q - a.q) * u,
  r: a.r + (b.r - a.r) * u,
});

function sampleTrack(id: number, t: Track, p: number): DrawnUnit | null {
  let state = t.base;
  let at: AxialPoint = state.coord;
  let moving = false;
  for (const s of t.segments) {
    if (p >= s.t1) {
      state = s.to;
      at = s.to.coord;
      continue;
    }
    if (p > s.t0) {
      const u = ease(progressIn([s.t0, s.t1], p));
      state = { ...(u < 0.5 ? s.from : s.to), facing: s.to.facing };
      at = lerp(s.from.coord, s.to.coord, u);
      moving = true;
    }
    break;
  }
  for (const l of t.lunges) {
    const u = progressIn(l.window, p);
    if (u <= 0 || u >= 1) continue;
    const out = Math.sin(Math.PI * u) * l.reach;
    at = lerp(at, l.toward, out);
  }
  // Fades play in time order, each taking over from the last: a unit whose
  // first fade is a fade-in is hidden until it starts.
  const fades = [...t.fades].sort((x, y) => x.window[0] - y.window[0]);
  let alpha = fades.length > 0 && fades[0].direction === "in" ? 0 : 1;
  for (const f of fades) {
    if (p < f.window[0]) break;
    const u = progressIn(f.window, p);
    alpha = f.direction === "in" ? u : 1 - u;
  }
  if (alpha <= 0) return null;
  let hp = state.hp;
  for (const h of t.hp) if (p >= h.t) hp = h.hp;
  return { ...state, id, hp, at, alpha, moving };
}

/**
 * The frame for beat `k` at `phase` (0..1). Phase 0 matches `snapshots[k]` and
 * phase 1 matches `snapshots[k + 1]` (same units, at their hexes, alpha 1).
 */
export function frameAt(pb: Playback, k: number, phase: number): Frame {
  if (k < 0 || k >= pb.beats.length) throw new RangeError(`no beat ${k} in ${pb.beats.length}`);
  const p = clamp01(phase);
  const plan = planFor(pb, k);
  const units: DrawnUnit[] = [];
  const drawnAt = new Map<number, AxialPoint>();
  for (const [id, t] of plan.tracks) {
    const u = sampleTrack(id, t, p);
    if (u === null) continue;
    units.push(u);
    drawnAt.set(id, u.at);
  }
  const effects: Effect[] = [];
  for (const e of plan.effects) {
    if (p < e.window[0] || p > e.window[1]) continue;
    const made = e.make(progressIn(e.window, p), (id) => drawnAt.get(id) ?? null);
    if (made !== null) effects.push(made);
  }
  return { beat: k, phase: p, units, effects };
}

// --- camera ------------------------------------------------------------------

export interface CameraHint {
  /** Hexes the viewer's own part of the beat touches. */
  hexes: Axial[];
  /** "fight": a combat, barrage or capture of theirs; "move": only their moves. */
  focus: "fight" | "move";
}

/**
 * Where the viewer's own action is in beat `k`, `null` if they have none.
 * Fights win over moves so the camera goes to the clash, not the whole army.
 */
export function cameraHint(pb: Playback, k: number): CameraHint | null {
  const beat = pb.beats[k];
  const mine = (id: number) => (pb.snapshots[k].get(id) ?? pb.snapshots[k + 1].get(id))?.owner === pb.viewer;
  const fights: Axial[] = [];
  const moves: Axial[] = [];
  for (const a of beat.actions) {
    switch (a.kind) {
      case "combat":
        if (a.sides.some((s) => s.owner === pb.viewer)) fights.push(a.at);
        break;
      case "barrage":
        if (mine(a.gunId) || a.strikes.some((s) => s.owner === pb.viewer)) fights.push(a.target, ...(a.from ? [a.from] : []));
        break;
      case "capture":
        if (a.owner === pb.viewer || a.previousOwner === pb.viewer) fights.push(a.at);
        break;
      case "move":
      case "deploy":
        if (mine(a.unitId)) moves.push(a.from, a.to);
        break;
      case "pack":
        if (mine(a.unitId)) moves.push(a.at);
        break;
      case "blocked":
        if (mine(a.unitId)) moves.push(a.from);
        break;
      case "recruit":
      case "disband":
        if (a.owner === pb.viewer) moves.push(a.at);
        break;
      default:
        break;
    }
  }
  if (fights.length > 0) return { hexes: fights, focus: "fight" };
  if (moves.length > 0) return { hexes: moves, focus: "move" };
  return null;
}

/**
 * A camera target for every beat: its own hint, or the last earlier beat's when
 * it has none (so the camera holds still through beats without the viewer's
 * action), or the first later one's at the start. `null` entries only when the
 * viewer acts in no beat at all.
 */
export function cameraTrack(pb: Playback): (CameraHint | null)[] {
  const own = pb.beats.map((_, k) => cameraHint(pb, k));
  const firstHint = own.find((h) => h !== null) ?? null;
  let carried: CameraHint | null = firstHint;
  return own.map((h) => {
    if (h !== null) carried = h;
    return carried;
  });
}

/** A hint's hexes as a pixel rectangle (hex centres padded by one hex) at hex `size`. */
export function hintBounds(hint: CameraHint, size: number): { x: number; y: number; width: number; height: number } {
  const points = hint.hexes.map((h) => axialToPixel(h.q, h.r, size));
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const x = Math.min(...xs) - size;
  const y = Math.min(...ys) - size;
  return { x, y, width: Math.max(...xs) + size - x, height: Math.max(...ys) + size - y };
}
