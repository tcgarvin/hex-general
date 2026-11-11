// Turn playback, step 2: the units to draw at every beat (WORLD_SPEC §11).
//
// ## The start-of-turn picture
//
// Playback needs the board as the viewer saw it when the turn began. Two ways:
//
//   (a) the viewer's previous replay (`GET /worlds/{id}/turns/{N-1}`): its
//       `state_after` is exactly the fogged state the viewer planned turn N on,
//       with every unit they could see then, including their own units that die
//       this turn and enemies that walk out of sight. One more small fetch.
//   (b) rewinding `state_after` through the events. This only knows units that
//       are still visible at the end or that some witnessed event names; an
//       enemy the viewer watched at the start and then lost sight of is simply
//       unknown, and units with no event before their first sighting have no
//       start position at all.
//
// We use (a) whenever it exists and fall back to (b) only for turn 0, which has
// no earlier replay (the API keeps no record of the initial state; the
// WorldView a player planned turn 0 on is gone once it resolves). Rewinding is
// exact for the viewer's own units (their events are never fogged while they
// live), which is what matters most.
//
// ## Fog
//
// The server fogs the replay with the END-of-turn sight: an event is kept iff it
// involves one of the viewer's units or touches a hex they can see once the
// turn is over (`app/engine/fog.py`). So mid-turn, units can come into or drop
// out of the picture without a full path. Reconstruction walks the beats
// forward from the start picture, applying each action, and adds *fog actions*
// where the log alone cannot explain what is drawn:
//
//   - `reveal`: a unit not drawn yet is named by an event (it fades in where the
//     event places it, just before the event), or is in `state_after` without
//     any event (it fades in at the beat from which the viewer's sight covers
//     its hex for the rest of the turn, using the visibility mirror).
//   - `conceal`: a drawn unit is gone at the end and did not die in view (it
//     fades out at the first beat its hex falls out of the viewer's sight).
//   - `relocate`: a drawn unit next turns up somewhere else (it moved unseen).
//   - `correct`: state the log does not account for, snapped at the end.
//
// After all beats the drawn units are exactly `state_after`'s (ids, coords,
// hp, posture, facing, disorder): the invariant the tests check on real turns.

import { hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";
import { UNIT_STATS } from "@/planning/rules";

import type { WorldReplay } from "../useTurnReport";
import type { UnitBook } from "../turnReport";
import { buildTimeline, type Beat, type BeatAction, type PlaybackUnit } from "./timeline";
import { visibleHexKeys, type TerrainAt } from "./visibility";

type WorldState = components["schemas"]["WorldState"];
type Unit = components["schemas"]["Unit"];
type Posture = components["schemas"]["Posture"];
type UnitType = components["schemas"]["UnitType"];

export type StartSource = "previous_turn" | "rewound";

export interface PlaybackInput {
  replay: WorldReplay;
  viewer: string;
  /**
   * The viewer's `state_after` of turn N-1 (start picture (a)); `null` exactly
   * when there is no earlier replay (turn 0), which rewinds instead.
   */
  before: WorldState | null;
  /** Terrain lookup for the visibility mirror (`WorldMap.hexAt(q, r)?.terrain`). */
  terrainAt: TerrainAt;
  book: UnitBook;
}

export interface Playback {
  turn: number;
  viewer: string;
  start: StartSource;
  /** The beats with fog actions added and gun origins / entrant hexes resolved. */
  beats: Beat[];
  /** `snapshots[b]`: the drawn units at boundary `b` (before beat `b`; `b = beats.length` is the end). */
  snapshots: ReadonlyMap<number, PlaybackUnit>[];
  /** The viewer's visible hex keys at each boundary (mirror of the engine's fog). */
  visible: ReadonlySet<string>[];
  /** Settlements whose owner changes this turn: hex key → owner at each boundary (`null`: neutral). */
  control: ReadonlyMap<string, string | null>[];
}

/** The units drawn at the start and at the end of beat `k`. */
export function unitsAtBeat(playback: Playback, k: number): { before: PlaybackUnit[]; after: PlaybackUnit[] } {
  if (k < 0 || k >= playback.beats.length) throw new RangeError(`no beat ${k} in ${playback.beats.length}`);
  return { before: [...playback.snapshots[k].values()], after: [...playback.snapshots[k + 1].values()] };
}

// --- applying actions -------------------------------------------------------

type Snapshot = Map<number, PlaybackUnit>;

const same = (a: Axial, b: Axial): boolean => a.q === b.q && a.r === b.r;

function stepped(u: PlaybackUnit, to: Axial): PlaybackUnit {
  // Facing follows the horizontal sign of the step; a vertical step keeps it (`movement._face`).
  const facing = to.q > u.coord.q ? "right" : to.q < u.coord.q ? "left" : u.facing;
  return { ...u, coord: to, facing };
}

/**
 * Apply one action to the drawn units, in place. A unit the action names but
 * that is not drawn is one the viewer cannot see (fog); its part is skipped.
 */
export function applyAction(snap: Snapshot, a: BeatAction): void {
  const update = (id: number, f: (u: PlaybackUnit) => PlaybackUnit) => {
    const u = snap.get(id);
    if (u !== undefined) snap.set(id, f(u));
  };
  switch (a.kind) {
    case "move":
      update(a.unitId, (u) => stepped(u, a.to));
      break;
    case "pack":
      update(a.unitId, (u) => ({ ...u, posture: "column" }));
      break;
    case "deploy":
      update(a.unitId, (u) => ({ ...u, coord: a.to, posture: "deployed", disordered: u.disordered || a.disordered }));
      break;
    case "combat":
      for (const s of a.sides) {
        if (s.destroyed) snap.delete(s.unitId);
        else update(s.unitId, (u) => ({ ...u, hp: s.hpAfter }));
      }
      if (a.entrantId !== null) update(a.entrantId, (u) => stepped(u, a.entrantTo ?? a.at));
      break;
    case "barrage":
      for (const s of a.strikes) {
        if (s.destroyed) snap.delete(s.unitId);
        else update(s.unitId, (u) => ({ ...u, hp: s.hpAfter }));
      }
      break;
    case "repair":
      update(a.unitId, (u) => ({ ...u, hp: a.hpAfter }));
      break;
    case "recruit":
      snap.set(a.unitId, {
        id: a.unitId,
        owner: a.owner,
        type: a.type,
        coord: a.at,
        hp: UNIT_STATS[a.type].maxHp,
        posture: "deployed",
        facing: a.facing,
        disordered: false,
      });
      break;
    case "disband":
    case "conceal":
      snap.delete(a.unitId);
      break;
    case "reveal":
    case "relocate":
    case "correct":
      snap.set(a.unit.id, a.unit);
      break;
    case "steady":
      update(a.unitId, (u) => ({ ...u, disordered: false }));
      break;
    case "blocked":
    case "jam":
    case "held":
    case "stuck":
    case "capture":
      break;
  }
}

/** Apply every action of `beat` to a copy of `snap`. */
export function applyBeat(snap: ReadonlyMap<number, PlaybackUnit>, beat: Beat): Map<number, PlaybackUnit> {
  const next = new Map(snap);
  for (const a of beat.actions) applyAction(next, a);
  return next;
}

// --- what each event tells us about a unit -----------------------------------

/**
 * What one action says about one unit. `pre`/`post` are its hex before and
 * after (`null`: the event does not say, e.g. an attacker's own hex); `hint` is
 * a hex to fall back on when nothing better is known (the fight's hex).
 */
interface Touch {
  unitId: number;
  pre: Axial | null;
  post: Axial | null;
  moves: boolean;
  hint: Axial | null;
  hpBefore: number | null;
  postureBefore: Posture | null;
  deploys: boolean;
  deploysDisordered: boolean;
  dies: boolean;
  creates: boolean;
}

function touch(unitId: number, fields: Partial<Omit<Touch, "unitId">>): Touch {
  return {
    unitId,
    pre: null,
    post: null,
    moves: false,
    hint: null,
    hpBefore: null,
    postureBefore: null,
    deploys: false,
    deploysDisordered: false,
    dies: false,
    creates: false,
    ...fields,
  };
}

const MAX_HP = (type: UnitType): number => UNIT_STATS[type].maxHp;

function touchesOf(a: BeatAction): Touch[] {
  switch (a.kind) {
    case "move":
      return [touch(a.unitId, { pre: a.from, post: a.to, moves: true })];
    case "pack":
      return [touch(a.unitId, { pre: a.at, post: a.at, postureBefore: "deployed" })];
    case "deploy":
      return [touch(a.unitId, { pre: a.from, post: a.to, moves: true, postureBefore: "column", deploys: true, deploysDisordered: a.disordered })];
    case "blocked":
      return [touch(a.unitId, { pre: a.from, post: a.from })];
    case "jam":
      return a.unitIds.map((id) => touch(id, { hint: a.at }));
    case "held":
    case "stuck":
      return [touch(a.unitId, { pre: a.at, post: a.at, postureBefore: "column" })];
    case "combat":
      return a.sides.map((s) => {
        const hpBefore = Math.min(MAX_HP(s.type), s.hpAfter + s.damageTaken);
        if (s.role === "defender") return touch(s.unitId, { pre: a.at, post: a.at, hpBefore, dies: s.destroyed });
        if (s.unitId === a.entrantId) return touch(s.unitId, { moves: true, post: a.at, hint: a.at, hpBefore });
        return touch(s.unitId, { hint: a.at, hpBefore, dies: s.destroyed });
      });
    case "barrage":
      return [
        touch(a.gunId, {}),
        ...a.strikes.map((s) =>
          touch(s.unitId, {
            pre: a.target,
            post: a.target,
            hpBefore: Math.min(MAX_HP(s.type), s.hpAfter + s.damage),
            dies: s.destroyed,
          }),
        ),
      ];
    case "capture":
      return [touch(a.unitId, { pre: a.at, post: a.at })];
    case "repair":
      return [touch(a.unitId, { hpBefore: a.hpBefore })];
    case "recruit":
      return [touch(a.unitId, { post: a.at, creates: true })];
    case "disband":
      return [touch(a.unitId, { pre: a.at, dies: true })];
    case "reveal":
    case "conceal":
    case "relocate":
    case "correct":
    case "steady":
      return [];
  }
}

// --- reconstruction ----------------------------------------------------------

interface Identity {
  owner: string;
  type: UnitType;
}

function toPlaybackUnit(u: Unit): PlaybackUnit {
  return {
    id: u.id,
    owner: u.owner,
    type: u.type,
    coord: u.coord,
    hp: u.hp,
    posture: u.posture,
    facing: u.facing,
    disordered: u.disordered,
  };
}

function identities(beats: readonly Beat[], states: readonly WorldState[]): Map<number, Identity> {
  const known = new Map<number, Identity>();
  for (const s of states) for (const u of s.units) known.set(u.id, { owner: u.owner, type: u.type });
  for (const b of beats) {
    for (const a of b.actions) {
      if (a.kind === "combat") for (const s of a.sides) known.set(s.unitId, { owner: s.owner, type: s.type });
      if (a.kind === "barrage") for (const s of a.strikes) known.set(s.unitId, { owner: s.owner, type: s.type });
      if (a.kind === "recruit" || a.kind === "disband") known.set(a.unitId, { owner: a.owner, type: a.type });
    }
  }
  return known;
}

/** Every unit's touches in playback order, with the beat each falls in. */
function touchIndex(beats: readonly Beat[]): Map<number, { beat: number; touch: Touch }[]> {
  const byUnit = new Map<number, { beat: number; touch: Touch }[]>();
  beats.forEach((b, k) => {
    for (const a of b.actions) {
      for (const t of touchesOf(a)) {
        const list = byUnit.get(t.unitId) ?? [];
        list.push({ beat: k, touch: t });
        byUnit.set(t.unitId, list);
      }
    }
  });
  return byUnit;
}

/**
 * A unit's state just before its touch `from` (all of them for `from = 0`),
 * read forward from what its later events and `state_after` say. `null` when
 * nothing places it on the map or its owner and type are unknown.
 */
function deriveState(
  id: number,
  touches: readonly { touch: Touch }[],
  from: number,
  identity: Identity | undefined,
  end: PlaybackUnit | undefined,
): PlaybackUnit | null {
  if (identity === undefined) return null;
  const rest = touches.slice(from).map((t) => t.touch);
  let coord: Axial | null = null;
  for (const t of rest) {
    if (t.creates) break;
    if (t.pre !== null) {
      coord = t.pre;
      break;
    }
    if (t.moves) {
      coord = t.post;
      break;
    }
  }
  coord ??= end?.coord ?? rest.find((t) => t.hint !== null)?.hint ?? null;
  if (coord === null) return null;
  const hp = rest.find((t) => t.hpBefore !== null)?.hpBefore ?? end?.hp ?? MAX_HP(identity.type);
  const posture = rest.find((t) => t.postureBefore !== null)?.postureBefore ?? end?.posture ?? "deployed";
  // Disorder held at the start expires at the end of the turn, so a unit that
  // deploys disordered yet ends steady must have started disordered.
  const disorderedDeploy = rest.some((t) => t.deploysDisordered);
  const disordered = disorderedDeploy
    ? end !== undefined && !end.disordered
    : rest.some((t) => t.deploys)
      ? false
      : (end?.disordered ?? false);
  return { id, owner: identity.owner, type: identity.type, coord, hp, posture, facing: end?.facing ?? "right", disordered };
}

interface Insertions {
  /** Fog actions to run at the start of a beat, before its own actions. */
  atStart: Map<number, BeatAction[]>;
  /** Fog actions to run after a beat's own actions. */
  atEnd: Map<number, BeatAction[]>;
}

function insert(into: Map<number, BeatAction[]>, beat: number, a: BeatAction): void {
  const list = into.get(beat) ?? [];
  list.push(a);
  into.set(beat, list);
}

interface Context {
  identity: Map<number, Identity>;
  touches: Map<number, { beat: number; touch: Touch }[]>;
  end: Map<number, PlaybackUnit>;
  viewer: string;
}

/**
 * Walk the raw beats forward from `start`, resolving what each action needs
 * from the board and adding the event-driven fog actions (`reveal` before a
 * unit's first witnessed event, `relocate` when an event finds it elsewhere).
 * Returns the resolved beats and the snapshot at every boundary.
 */
function walk(
  raw: readonly Beat[],
  start: ReadonlyMap<number, PlaybackUnit>,
  ctx: Context,
  ins: Insertions,
): { beats: Beat[]; snapshots: Map<number, PlaybackUnit>[] } {
  const snap = new Map(start);
  const snapshots: Map<number, PlaybackUnit>[] = [new Map(snap)];
  const seen = new Map<number, number>(); // touches consumed per unit
  const dead = new Set<number>();
  const beats = raw.map((beat, k) => {
    const actions: BeatAction[] = [];
    const run = (a: BeatAction) => {
      actions.push(a);
      applyAction(snap, a);
    };
    for (const a of ins.atStart.get(k) ?? []) run(a);
    for (const a of beat.actions) {
      for (const t of touchesOf(a)) {
        const n = seen.get(t.unitId) ?? 0;
        seen.set(t.unitId, n + 1);
        if (t.creates || dead.has(t.unitId)) continue;
        const current = snap.get(t.unitId);
        const list = ctx.touches.get(t.unitId)!;
        const derived = deriveState(t.unitId, list, n, ctx.identity.get(t.unitId), ctx.end.get(t.unitId));
        if (current === undefined) {
          if (derived !== null) run({ kind: "reveal", unit: derived });
        } else if (t.pre !== null && !same(current.coord, t.pre)) {
          run({ kind: "relocate", unit: { ...current, coord: t.pre, posture: derived?.posture ?? current.posture } });
        }
      }
      run(resolve(a, snap, ctx));
      for (const t of touchesOf(a)) if (t.dies) dead.add(t.unitId);
    }
    for (const a of ins.atEnd.get(k) ?? []) run(a);
    snapshots.push(new Map(snap));
    return { ...beat, actions };
  });
  return { beats, snapshots };
}

/** Fill in what an action needs from the board: the gun's hex, the entrant's hex, a recruit's facing. */
function resolve(a: BeatAction, snap: ReadonlyMap<number, PlaybackUnit>, ctx: Context): BeatAction {
  switch (a.kind) {
    case "barrage":
      return { ...a, from: snap.get(a.gunId)?.coord ?? null };
    case "combat": {
      if (a.entrantId === null) return a;
      const entrant = snap.get(a.entrantId);
      // An edge swap reports the hex of one side; if that is the entrant's own
      // hex, it took the other side's hex (`movement._resolve_one_swap`).
      if (entrant !== undefined && same(entrant.coord, a.at)) {
        const other = a.sides.find((s) => s.unitId !== a.entrantId);
        const otherAt = other === undefined ? undefined : snap.get(other.unitId)?.coord;
        return { ...a, entrantTo: otherAt ?? a.at };
      }
      return { ...a, entrantTo: a.at };
    }
    case "recruit":
      return { ...a, facing: ctx.end.get(a.unitId)?.facing ?? "right" };
    default:
      return a;
  }
}

/**
 * Owners at each boundary of the settlements whose owner changes this turn
 * (hex key → owner, `null`: neutral). A witnessed capture flips at its beat; a
 * change the viewer did not witness (a site of theirs taken out of their
 * sight) shows only at the end, where `state_after` is authoritative.
 */
function controlTimeline(
  beats: readonly Beat[],
  before: WorldState["settlements"] | null,
  after: WorldState["settlements"],
): Map<string, string | null>[] {
  const captures = beats.flatMap((b, k) =>
    b.actions.flatMap((a) => (a.kind === "capture" ? [{ k, key: hexKey(a.at.q, a.at.r), a }] : [])),
  );
  const final = new Map(after.map((s) => [hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null]));
  const initial = new Map<string, string | null>();
  for (const s of before ?? []) {
    const key = hexKey(s.coord.q, s.coord.r);
    if ((s.controlled_by ?? null) !== (final.get(key) ?? null)) initial.set(key, s.controlled_by ?? null);
  }
  for (const { key, a } of captures) if (!initial.has(key)) initial.set(key, a.previousOwner);
  return Array.from({ length: beats.length + 1 }, (_, boundary) => {
    const owners = new Map<string, string | null>();
    for (const [key, owner] of initial) {
      if (boundary === beats.length) {
        owners.set(key, final.get(key) ?? null);
        continue;
      }
      const taken = captures.filter((c) => c.key === key && c.k < boundary).at(-1);
      owners.set(key, taken === undefined ? owner : taken.a.owner);
    }
    return owners;
  });
}

function settlementsAt(
  endSettlements: WorldState["settlements"],
  control: ReadonlyMap<string, string | null>,
): WorldState["settlements"] {
  return endSettlements.map((s) => {
    const key = hexKey(s.coord.q, s.coord.r);
    return control.has(key) ? { ...s, controlled_by: control.get(key) } : s;
  });
}

function rewoundStart(ctx: Context): Map<number, PlaybackUnit> {
  const start = new Map<number, PlaybackUnit>();
  for (const [id, identity] of ctx.identity) {
    if (identity.owner !== ctx.viewer) continue;
    const list = ctx.touches.get(id) ?? [];
    if (list.length > 0 && list[0].touch.creates) continue; // recruited this turn
    if (list.length === 0 && !ctx.end.has(id)) continue;
    const derived = deriveState(id, list, 0, identity, ctx.end.get(id));
    if (derived !== null) start.set(id, derived);
  }
  return start;
}

const differs = (a: PlaybackUnit, b: PlaybackUnit): boolean =>
  !same(a.coord, b.coord) ||
  a.hp !== b.hp ||
  a.posture !== b.posture ||
  a.facing !== b.facing ||
  a.disordered !== b.disordered ||
  a.owner !== b.owner ||
  a.type !== b.type;

/** Build the full playback for one viewer's replay of one turn. */
export function buildPlayback(input: PlaybackInput): Playback {
  const { replay, viewer, before, terrainAt, book } = input;
  const timeline = buildTimeline(replay.events, book);
  const raw: Beat[] =
    timeline.length > 0 ? timeline : [{ phase: "settle", pulse: null, label: "End of turn", actions: [], eventIndices: [], lines: [] }];
  const end = new Map(replay.state_after.units.map((u) => [u.id, toPlaybackUnit(u)]));
  const ctx: Context = {
    identity: identities(raw, before === null ? [replay.state_after] : [before, replay.state_after]),
    touches: touchIndex(raw),
    end,
    viewer,
  };
  const start: Map<number, PlaybackUnit> =
    before === null ? rewoundStart(ctx) : new Map(before.units.map((u) => [u.id, toPlaybackUnit(u)]));
  const B = raw.length;
  const last = B - 1;

  // Disorder held at the start expires at the end of this turn (`execution.py`).
  const ins: Insertions = { atStart: new Map(), atEnd: new Map() };
  for (const u of start.values()) if (u.disordered) insert(ins.atEnd, last, { kind: "steady", unitId: u.id });

  // Pass 1: events only. The viewer's own units are complete after it, so their
  // sight per boundary is final.
  let pass = walk(raw, start, ctx, ins);
  const control = controlTimeline(pass.beats, before?.settlements ?? null, replay.state_after.settlements);
  const visible = pass.snapshots.map((snap, b) =>
    visibleHexKeys(snap.values(), settlementsAt(replay.state_after.settlements, control[b]), viewer, terrainAt),
  );
  const seenThrough = (coord: Axial, from: number): number => {
    // The first boundary >= from at which `coord` drops out of sight; B + 1 if never.
    const key = hexKey(coord.q, coord.r);
    for (let b = from; b <= B; b++) if (!visible[b].has(key)) return b;
    return B + 1;
  };
  const sightedFrom = (coord: Axial): number => {
    // The earliest boundary from which `coord` stays in sight to the end.
    const key = hexKey(coord.q, coord.r);
    let b = B;
    while (b > 0 && visible[b - 1].has(key)) b--;
    return b;
  };
  const lastChange = (id: number): number => {
    // The last beat in which anything happened to the unit (-1: nothing did).
    let k = -1;
    pass.beats.forEach((beat, i) => {
      if (beat.actions.some((a) => touchesOf(a).some((t) => t.unitId === id) || ("unit" in a && a.unit.id === id))) k = i;
    });
    return k;
  };
  const concealBeat = (u: PlaybackUnit): number => {
    const b = seenThrough(u.coord, lastChange(u.id) + 1);
    return Math.max(0, Math.min(last, b - 1));
  };

  // Pass 2 decisions: sightings, departures and unseen moves the events do not show.
  const finalSnap = pass.snapshots[B];
  const promoted = new Map<number, PlaybackUnit>();
  for (const [id, u] of end) {
    const drawn = finalSnap.get(id);
    if (drawn === undefined) {
      const from = sightedFrom(u.coord);
      if (before === null && from === 0) promoted.set(id, u);
      else insert(ins.atStart, Math.max(0, Math.min(last, from - 1)), { kind: "reveal", unit: u });
    } else if (!same(drawn.coord, u.coord)) {
      if (u.owner === viewer) {
        insert(ins.atEnd, last, { kind: "relocate", unit: u });
        continue;
      }
      const leaves = concealBeat(drawn);
      const arrives = Math.max(leaves, Math.min(last, sightedFrom(u.coord) - 1));
      if (arrives === leaves) insert(ins.atEnd, leaves, { kind: "relocate", unit: u });
      else {
        insert(ins.atEnd, leaves, { kind: "conceal", unitId: id });
        insert(ins.atStart, arrives, { kind: "reveal", unit: u });
      }
    }
  }
  for (const [id, drawn] of finalSnap) {
    if (!end.has(id)) insert(ins.atEnd, concealBeat(drawn), { kind: "conceal", unitId: id });
  }
  if (before === null) {
    // Rewinding: an enemy first named mid-turn that stood in sight from the
    // start was there all along, so it belongs in the start picture.
    pass.beats.forEach((beat, k) => {
      for (const a of beat.actions) {
        if (a.kind !== "reveal" || a.unit.owner === viewer) continue;
        const key = hexKey(a.unit.coord.q, a.unit.coord.r);
        if (visible.slice(0, k + 1).every((v) => v.has(key))) promoted.set(a.unit.id, a.unit);
      }
    });
  }
  for (const [id, u] of promoted) start.set(id, u);

  pass = walk(raw, start, ctx, ins);
  // Safety net: whatever still disagrees with `state_after` snaps at the end.
  const residue = [...end.values()].filter((u) => {
    const drawn = pass.snapshots[B].get(u.id);
    return drawn === undefined || differs(drawn, u);
  });
  if (residue.length > 0) {
    for (const u of residue) insert(ins.atEnd, last, { kind: "correct", unit: u });
    pass = walk(raw, start, ctx, ins);
  }

  const finalControl = controlTimeline(pass.beats, before?.settlements ?? null, replay.state_after.settlements);
  return {
    turn: replay.turn,
    viewer,
    start: before === null ? "rewound" : "previous_turn",
    beats: pass.beats,
    snapshots: pass.snapshots,
    // Recomputed: an own unit relocated at the end moves the viewer's sight.
    visible: pass.snapshots.map((snap, b) =>
      visibleHexKeys(snap.values(), settlementsAt(replay.state_after.settlements, finalControl[b]), viewer, terrainAt),
    ),
    control: finalControl,
  };
}
