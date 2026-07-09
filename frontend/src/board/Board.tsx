// Phase 8 board + Phase 9 planning input. Renders a fog-filtered GameState as
// flat-colour hex tiles + tinted unit markers, with pan/zoom and click→(q,r)
// hit-testing. When a `planning` controller is supplied (your move), clicks drive
// unit selection / exact-path building and the board overlays the movement range
// and staged paths. No sprites yet — terrain is solid fills (see palette.ts);
// Phase 8 Slice 2 swaps Graphics for the AI-generated sprite set.

import { Application, extend } from "@pixi/react";
import { Container, Graphics, NineSliceSprite, Sprite, Text, TextStyle, TilingSprite } from "pixi.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { components } from "@/lib/api/schema";
import { defaultFacing } from "@/planning/facing";
import { reachableContinuations } from "@/planning/reach";
import type { Planning } from "@/planning/usePlanning";
import type { PlanAdvisory, UnitWarning } from "@/planning/warnings";
import type { LogBeat } from "@/replay/replayLog";

import { moveArrowOutlines, type ArrowDims } from "./arrows";
import {
  AXIAL_DIRECTIONS,
  axialEqual,
  axialToPixel,
  hexCornerPoints,
  HEX_SIZE,
  hexKey,
  mapPixelBounds,
  pixelToAxial,
  type Axial,
  type MapBounds,
} from "./hex";
import {
  BOTTOM_HEIGHT,
  fullMapLayout,
  LOCK_WIDTH,
  pointInRect,
  replayLayout,
  surfaceLayout,
  type Rect,
  type SurfaceLayout,
} from "./layout";
import {
  HEX_STROKE,
  NEUTRAL_COLOR,
  playerColor,
  TERRAIN_FILL,
} from "./palette";
import { loadChromeTextures, type LoadedChrome } from "./panels/chrome";
import { GameOverPanel, type GameOutcome } from "./panels/GameOverPanel";
import type { InspectedSettlement } from "./panels/inspector";
import { InspectorPanel } from "./panels/InspectorPanel";
import { LockPanel } from "./panels/LockPanel";
import { RecruitPanel } from "./panels/RecruitPanel";
import {
  canRecruit,
  recruitAvailability,
  recruitButtonAt,
  recruitButtons,
} from "./panels/recruitCity";
import {
  ReplayControlPanel,
  type ReplayControlsProps,
} from "./panels/ReplayControlPanel";
import { replayButtonAt, type ReplayAction } from "./panels/replayControls";
import { ReplayLogPanel } from "./panels/ReplayLogPanel";
import { beatIndexAtY, logViewportHeight, maxScroll, measureReplayLog } from "./panels/replayLogLayout";
import { RosterPanel } from "./panels/RosterPanel";
import { orderLabelOf, replayRosterRows, rosterRowAt, rosterRows } from "./panels/roster";
import { SelectedUnitPanel, type PortraitTile } from "./panels/SelectedUnitPanel";
import { paletteButtonAt, type PaletteAction } from "./panels/selectedUnit";
import { orientedRiverTile, riverCourseEdges } from "./riverTiles";
import { roadTileRefs, type RoadTileRef } from "./roadTiles";
import { loadTerrainTextures, variantIndex, type LoadedTiles } from "./tiles";
import {
  drawPipBar,
  hudVisibleAtZoom,
  ORDER_BADGE_Y,
  showsHealth,
} from "./unitHud";
import { loadUnitTextures, type LoadedUnits } from "./units";

extend({ Container, Graphics, NineSliceSprite, Sprite, Text, TilingSprite });

type Facing = components["schemas"]["Facing"];
type GameState = components["schemas"]["GameState"];
type Hex = components["schemas"]["Hex"];
type Terrain = components["schemas"]["Terrain"];
type Unit = components["schemas"]["Unit"];
type UnitType = components["schemas"]["UnitType"];

const BACKGROUND = 0x0a0a0b;
// Zoom clamps are halved relative to the old 32px geometry: with HEX_SIZE
// doubled (16→32) the same on-screen size needs half the zoom factor, so halving
// these keeps fit/zoom-in/zoom-out behaviour identical while still permitting
// zoom to the tiles' native 64px resolution.
const MIN_ZOOM = 0.125;
const MAX_ZOOM = 2;
const DRAG_THRESHOLD_PX = 4;

// --- auto-camera (frame own units on load; glide to a roster pick if off-screen) ---
// Fraction of the map rectangle kept as breathing room around auto-fitted content,
// so framed units don't sit flush against the panel edges.
const FIT_PADDING = 0.12;
// The on-load fit won't zoom in past this — keeps a lone or tightly-clustered force
// from filling the whole view at an absurd magnification.
const INITIAL_MAX_ZOOM = 0.75;
// A unit counts as "comfortably in frame" when it sits within the map rectangle
// inset by this fraction on each side; selecting one nearer the edge glides it back
// toward centre.
const COMFORT_MARGIN = 0.15;
// Duration (ms) of a reveal glide.
const CAMERA_GLIDE_MS = 350;
// How far past the map's outer edge the camera may scroll — about one hex radius
// of dead space, just enough to bring edge hexes off the panels without drifting
// into a sea of background.
const PAN_OVERSCROLL = HEX_SIZE;

const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

// Planning overlay colours (range outline + staged/preview paths).
const RANGE_OUTLINE = 0xffe066;
const PATH_COLOR = 0xffe066;
const PATH_STAGED_COLOR = 0xc9a227;
const PREVIEW_COLOR = 0xfff4c2;
// Barrage overlay (target range + staged shot), in a distinct red/orange.
const BARRAGE_RANGE = 0xff7043;
const BARRAGE_COLOR = 0xff5252;
// A planned path "churns red" from the hex where enemy contact is expected to
// impede progress (an illegal ZOC→ZOC step, SPEC §8.4): the unit halts before
// that hex, so it and the unreachable remainder draw red instead of yellow.
const PATH_BLOCKED_COLOR = 0xff5252;
const PATH_BLOCKED_STAGED_COLOR = 0xb83434;
// Advisory overlay (SPEC §16.1): friendly traffic jam marker.
const TRAFFIC_COLOR = 0xffa726;

// Staged orders draw as tapered ribbon arrows ABOVE the unit sprites — narrow
// at the ordered unit, widening into a round-cornered head at the destination
// (the corner rounding comes from the round-join edge stroke). A translucent
// white casing rims the whole arrow so it stays legible over sprites and
// bright terrain. Dimensions are world px; the selected unit's arrow is
// heavier than other staged ones.
const ARROW_DIMS: ArrowDims = {
  startOffset: 22,
  tailHalf: 2,
  headBaseHalf: 7,
  headLen: 22,
  headHalf: 14,
  headNose: 4,
};
const ARROW_DIMS_STAGED: ArrowDims = {
  startOffset: 22,
  tailHalf: 1.5,
  headBaseHalf: 5,
  headLen: 18,
  headHalf: 11,
  headNose: 3,
};
// Hover preview: a slim un-tapered arrow for the segment a click would append.
const ARROW_DIMS_PREVIEW: ArrowDims = {
  startOffset: 4,
  tailHalf: 2.2,
  headBaseHalf: 2.2,
  headLen: 12,
  headHalf: 7,
  headNose: 2,
};
// Round-join stroke drawn around each ribbon fill in its own colour: it exists
// to round the polygon's corners, and widens the arrow by half its width.
const ARROW_EDGE = 3;
const ARROW_CASING = 0xffffff;
const ARROW_CASING_ALPHA = 0.55;
const ARROW_CASING_ALPHA_STAGED = 0.3;
// How far the casing rim extends past the colored edge stroke.
const ARROW_CASING_RIM = 2.5;
// Movement-pulse badge (a numbered disc on each hex the path enters).
const PULSE_BADGE_RADIUS = 9;
const PULSE_BADGE_FILL = 0x14141a;
// Fog of war (SPEC §9): hexes outside the viewer's vision are veiled with this
// translucent dark wash so lit hexes read at full brightness against them.
const FOG_COLOR = 0x05060a;
const FOG_ALPHA = 0.55;

// Stable empty path so unkeyed lookups don't churn memo identities.
const EMPTY_PATH: readonly Axial[] = [];
// Stable empty warnings, so an unwarned selection doesn't churn the panel.
const EMPTY_WARNINGS: readonly UnitWarning[] = [];

// Font sizes and stroke widths below are in world pixels and don't key off
// HEX_SIZE, so they're doubled alongside the 16→32 HEX_SIZE bump to keep their
// apparent size constant at the now-halved zoom.
const hpStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 22,
  fontWeight: "bold",
  fill: 0xffffff,
  stroke: { color: 0x000000, width: 6 },
});

// Badge over a unit with a staged rest/hold order (move/barrage draw geometry).
const orderBadgeStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 18,
  fontWeight: "bold",
  fill: 0xffe066,
  stroke: { color: 0x000000, width: 6 },
});

// Movement-pulse numerals inside the path badges ("night coin": dark disc,
// gold numeral). The grey variant marks pulses past the expected ZOC stop.
const pulseBadgeStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 13,
  fontWeight: "bold",
  fill: 0xffe066,
});
const pulseBadgeBlockedStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 13,
  fontWeight: "bold",
  fill: 0x8f8f96,
});

// The "+" marking a staged recruit's ghost preview on its city hex.
const recruitGhostStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 28,
  fontWeight: "bold",
  fill: 0xffffff,
  stroke: { color: 0x000000, width: 6 },
});

interface View {
  x: number;
  y: number;
  zoom: number;
}

/**
 * A {@link View} that fits `bounds` (a world-pixel box) centred inside the `map`
 * rectangle, zoomed in as far as `maxZoom` permits while leaving {@link FIT_PADDING}
 * breathing room on every side.
 */
function fitView(bounds: MapBounds, map: Rect, maxZoom: number): View {
  const availWidth = map.width * (1 - 2 * FIT_PADDING);
  const availHeight = map.height * (1 - 2 * FIT_PADDING);
  const zoom = Math.max(
    MIN_ZOOM,
    Math.min(maxZoom, availWidth / bounds.width, availHeight / bounds.height),
  );
  return {
    zoom,
    x: map.x + map.width / 2 - (bounds.minX + bounds.width / 2) * zoom,
    y: map.y + map.height / 2 - (bounds.minY + bounds.height / 2) * zoom,
  };
}

/**
 * Clamp one axis of a {@link View} so the map can't be scrolled more than
 * `overscroll` world units past its content. `view` is the pan offset on this
 * axis, `mapStart`/`mapLen` the visible map rectangle on it, and
 * `contentMin`/`contentMax` the map content's world extent. When the content
 * (plus overscroll) is narrower than the viewport, it's centred instead.
 */
function clampViewAxis(
  view: number,
  zoom: number,
  mapStart: number,
  mapLen: number,
  contentMin: number,
  contentMax: number,
  overscroll: number,
): number {
  const lo = mapStart + mapLen - (contentMax + overscroll) * zoom;
  const hi = mapStart - (contentMin - overscroll) * zoom;
  if (lo > hi) return (lo + hi) / 2;
  return Math.min(hi, Math.max(lo, view));
}

/**
 * Per-beat animation overlays for replay (Phase 10 Slice 3). Supplied while a
 * beat is animating; `undefined` when the board is in a settled state.
 *
 * The board always renders `state` (the pre-beat snapshot during animation, the
 * post-beat snapshot after). Overlays are drawn on top:
 *   - `slideOverrides`: pixel positions for units currently mid-slide, plus the
 *     facing of the step being animated (the settled post-beat facing, applied
 *     from slide start so a unit faces its direction of travel)
 *   - `fadingOut`: alpha 1→0 for units destroyed this beat (still in `state.units`)
 *   - `fadingIn`: alpha 0→1 for units recruited this beat (not in `state.units`)
 */
export interface ReplayAnimOverrides {
  slideOverrides: ReadonlyMap<number, { x: number; y: number; facing: Facing }>;
  fadingOut: ReadonlyMap<number, number>;
  fadingIn: ReadonlyMap<number, { unit: Unit; alpha: number }>;
}

/** Lock-control state + action (UI-SPEC §11), supplied throughout your turn. */
export interface LockState {
  ordered: number;
  total: number;
  idle: number;
  submitting: boolean;
  error: string | null;
  /** Your orders are already locked: the control reads "LOCKED" and does nothing. */
  locked: boolean;
  onLock: () => void;
}

export interface BoardProps {
  state: GameState;
  /** The viewer's slot; reserved for own-unit emphasis in later slices. */
  yourSlot: string;
  /**
   * Hexes the viewer's units currently illuminate (SPEC §9). Everything outside
   * this set is drawn dimmed as fog of war. The map is public, so this is purely a
   * rendering hint — when omitted (e.g. replay), nothing is dimmed.
   */
  visible?: readonly Axial[];
  /** Planning state, supplied throughout the viewer's active turn (incl. once locked). */
  planning?: Planning;
  /**
   * The viewer has locked their orders: the panels stay docked but turn read-only
   * — order edits are suppressed and the action buttons render greyed out.
   */
  locked?: boolean;
  /** Advisory ZOC/traffic predictions, supplied alongside `planning` (SPEC §16.1). */
  advisory?: PlanAdvisory;
  /** Lock control, supplied alongside `planning` (UI-SPEC §11). */
  lock?: LockState;
  /**
   * The match is decided: render a win/lose/draw indicator where the lock control
   * sits, instead of any planning chrome (UI-SPEC §11). Mutually exclusive with
   * `planning`/`lock` — there are no more turns to plan.
   */
  gameOver?: { outcome: GameOutcome; reason: string };
  /**
   * Replay controls: when supplied, switches the board into replay mode — the
   * roster shows post-turn units (read-only), and the full-width bottom dock
   * renders the beat playback controls instead of the order palette/lock.
   * Mutually exclusive with `planning`/`lock`/`gameOver`.
   */
  replayControls?: ReplayControlsProps;
  /** Per-beat animation overlays (Phase 10 Slice 3). See {@link ReplayAnimOverrides}. */
  replayAnim?: ReplayAnimOverrides;
  /**
   * Replay event log (UI-SPEC §13.3): the beat-grouped, scrollable log in the
   * right portion of the bottom dock. Supplied alongside `replayControls`. Board
   * owns the scroll offset; `onJumpToBeat` pauses + seeks when a row is clicked.
   */
  replayLog?: {
    logBeats: LogBeat[];
    onJumpToBeat: (beatIndex: number) => void;
  };
}

export type { ReplayControlsProps };

export function Board({ state, yourSlot, visible, planning, locked = false, advisory, lock, gameOver, replayControls, replayAnim, replayLog }: BoardProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [selected, setSelected] = useState<Axial | null>(null);

  const hexes = useMemo(() => state.map.hexes ?? [], [state.map.hexes]);
  const units = useMemo(() => state.units ?? [], [state.units]);
  // Fog of war: hexes the viewer can't currently see are dimmed. An undefined
  // `visible` (e.g. replay) means "no fog" — every hex draws at full brightness.
  const visibleKeys = useMemo<ReadonlySet<string> | null>(() => {
    if (visible === undefined) return null;
    const s = new Set<string>();
    for (const c of visible) s.add(hexKey(c.q, c.r));
    return s;
  }, [visible]);
  const settlements = useMemo(() => state.settlements ?? [], [state.settlements]);

  // --- docked-panel layout (UI-SPEC §2) ---
  // Planning and replay both show the roster + bottom dock; outside those modes
  // the map fills the whole surface.
  const showPanels = planning !== undefined || replayControls !== undefined;
  const layout = useMemo<SurfaceLayout | null>(() => {
    if (size === null) return null;
    if (replayControls !== undefined) return replayLayout(size.width, size.height);
    if (planning !== undefined) return surfaceLayout(size.width, size.height);
    return fullMapLayout(size.width, size.height);
  }, [size, planning, replayControls]);

  // --- replay event-log scroll (UI-SPEC §13.3) ---
  const logMeasure = useMemo(
    () => (replayLog !== undefined ? measureReplayLog(replayLog.logBeats) : null),
    [replayLog],
  );
  const [logScroll, setLogScroll] = useState(0);
  const currentBeat = replayControls?.currentBeat ?? -1;
  // Keep the scroll in sync without an effect: reset to the top for a new log, and
  // auto-scroll to keep the current beat in view as playback advances. This is the
  // React "adjust state from previous renders" pattern — a guarded setState during
  // render, which avoids the cascading re-renders of doing it in an effect.
  const [logSync, setLogSync] = useState<{ beats: LogBeat[] | null; beat: number }>({
    beats: null,
    beat: -1,
  });
  if (replayLog !== undefined && logMeasure !== null && layout !== null) {
    const logsChanged = logSync.beats !== replayLog.logBeats;
    const beatChanged = logSync.beat !== currentBeat;
    if (logsChanged || beatChanged) {
      setLogSync({ beats: replayLog.logBeats, beat: currentBeat });
      const viewportH = logViewportHeight(layout.replayLog.height);
      const limit = maxScroll(logMeasure.contentHeight, viewportH);
      const block = logMeasure.beats.find((b) => b.beatIndex === currentBeat);
      if (logsChanged || block === undefined) {
        setLogScroll(0);
      } else {
        setLogScroll((s) => {
          if (block.top < s) return Math.min(block.top, limit);
          const bottom = block.top + block.height;
          if (bottom > s + viewportH) return Math.min(bottom - viewportH, limit);
          return Math.min(s, limit);
        });
      }
    }
  }

  const ownUnits = useMemo(
    () => units.filter((u) => u.owner === yourSlot),
    [units, yourSlot],
  );
  // Hexes held by an enemy unit — used to derive the roster's ATTACK label.
  const enemyHexKeys = useMemo<ReadonlySet<string>>(() => {
    const s = new Set<string>();
    for (const u of units) if (u.owner !== yourSlot) s.add(hexKey(u.coord.q, u.coord.r));
    return s;
  }, [units, yourSlot]);
  const rows = useMemo(() => {
    if (planning !== undefined) {
      return rosterRows(ownUnits, planning, enemyHexKeys, advisory?.problemUnitIds);
    }
    if (replayControls !== undefined) {
      return replayRosterRows(ownUnits);
    }
    return [];
  }, [planning, replayControls, ownUnits, enemyHexKeys, advisory]);
  const [rosterHover, setRosterHover] = useState<number | null>(null);

  const hexByKey = useMemo(() => {
    const m = new Map<string, Hex>();
    for (const h of hexes) m.set(hexKey(h.coord.q, h.coord.r), h);
    return m;
  }, [hexes]);

  // Terrain probe for river/bridge edge-mask derivation; undefined means "off the
  // map / no hex here", a real answer (see `connectedEdges` in `riverTiles.ts`).
  const terrainAt = useCallback(
    (q: number, r: number): Terrain | undefined => hexByKey.get(hexKey(q, r))?.terrain,
    [hexByKey],
  );

  // Course-pruned river/bridge edge masks (junction triangle chords dropped) —
  // the whole-map derivation runs once, then each hex looks up its mask.
  const riverCourse = useMemo(
    () =>
      riverCourseEdges(
        hexes.map((h) => h.coord),
        terrainAt,
      ),
    [hexes, terrainAt],
  );

  // Baked road tile references for road-flagged hexes (T5). Whether the
  // referenced texture exists decides baked-vs-vector per hex at render time.
  const roadTiles = useMemo(
    () => roadTileRefs(hexes, (q, r) => hexByKey.get(hexKey(q, r))),
    [hexes, hexByKey],
  );

  const settlementOwner = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const s of settlements) m.set(hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null);
    return m;
  }, [settlements]);

  // --- planning overlays (only when a controller is supplied) ---
  const [hoverKey, setHoverKey] = useState<string | null>(null);
  // The hovered hex for the top-right inspector (UI-SPEC §7). Tracked separately
  // from `hoverKey`, which the route preview gates off outside editable move
  // mode — the inspector stays live in every planning state, locked included.
  const [inspectKey, setInspectKey] = useState<string | null>(null);
  // AI-generated terrain tile textures; null until loaded (flat colours until then).
  // Each terrain holds one or more interchangeable variants (see `tiles.ts`).
  const [terrainTex, setTerrainTex] = useState<LoadedTiles | null>(null);
  // Two-layer unit sprites; null until loaded (vector markers until then).
  const [unitTex, setUnitTex] = useState<LoadedUnits | null>(null);
  const [chromeTex, setChromeTex] = useState<LoadedChrome | null>(null);
  // Palette button feedback (UI-STYLE §4): which button the pointer is over,
  // and which one is held down. Purely visual — actions still fire on the
  // pointer-up path below.
  const [paletteHover, setPaletteHover] = useState<PaletteAction | null>(null);
  const [palettePressed, setPalettePressed] = useState<PaletteAction | null>(null);
  // Replay-control keycap held down (visual pressed art only, same pattern).
  const [replayPressed, setReplayPressed] = useState<ReplayAction | null>(null);

  const selectedUnitId = planning?.selectedUnitId ?? null;
  const selectedCity = planning?.selectedCity ?? null;
  const mode = planning?.mode ?? "move";

  // Recruit economy inputs (the panel does the per-city budget/cap math itself).
  const bank = useMemo(
    () => (state.players ?? []).find((p) => p.id === yourSlot)?.bank ?? 0,
    [state.players, yourSlot],
  );
  const unitCap = state.config.unit_cap;
  const selectedUnit = useMemo(() => {
    if (selectedUnitId === null) return null;
    return units.find((u) => u.id === selectedUnitId) ?? null;
  }, [selectedUnitId, units]);

  // The selected unit's committed move path (the prefix new routing continues from).
  const selectedPath = useMemo<readonly Axial[]>(() => {
    if (selectedUnitId === null) return EMPTY_PATH;
    const order = planning?.stagedByUnit[selectedUnitId];
    return order !== undefined && order.kind === "move" ? order.path : EMPTY_PATH;
  }, [selectedUnitId, planning?.stagedByUnit]);

  // Every hex the selected unit can still route to, mapped to the full route.
  // Only in move mode — while aiming a barrage, the move range is hidden. Once
  // locked there is no routing to do, so the range outline + hover preview are
  // suppressed (the committed path itself still draws).
  const continuations = useMemo(
    () =>
      locked || selectedUnit === null || mode !== "move"
        ? null
        : reachableContinuations(selectedUnit, selectedPath, hexByKey),
    [locked, selectedUnit, mode, selectedPath, hexByKey],
  );

  // The route a click at the hovered hex would commit (origin → hover). Resolved
  // against the current selection's routes, so a stale hover simply yields no
  // preview rather than a wrong one.
  const previewPath =
    hoverKey !== null ? (continuations?.get(hoverKey) ?? null) : null;

  // The selected unit's own hex tile, for the dock portrait's terrain backdrop
  // (null until tiles load — the portrait keeps its plain backdrop).
  const selectedUnitTile = useMemo<PortraitTile | null>(() => {
    if (selectedUnit === null || terrainTex === null) return null;
    const h = hexByKey.get(hexKey(selectedUnit.coord.q, selectedUnit.coord.r));
    if (h === undefined) return null;
    return hexTileRef(h, terrainTex, riverCourse, roadTiles);
  }, [selectedUnit, terrainTex, hexByKey, riverCourse, roadTiles]);

  // Load the terrain tile textures once; the board renders flat colours until they
  // arrive. A load failure leaves `terrainTex` null (flat-colour fallback) and is
  // surfaced to the console rather than blanking the map.
  useEffect(() => {
    let active = true;
    loadTerrainTextures().then(
      (tex) => {
        if (active) setTerrainTex(tex);
      },
      (err) => {
        console.error("Failed to load terrain tile textures", err);
      },
    );
    loadUnitTextures().then(
      (tex) => {
        if (active) setUnitTex(tex);
      },
      (err) => {
        console.error("Failed to load unit sprite textures", err);
      },
    );
    loadChromeTextures().then(
      (tex) => {
        if (active) setChromeTex(tex);
      },
      (err) => {
        console.error("Failed to load UI chrome textures", err);
      },
    );
    return () => {
      active = false;
    };
  }, []);

  // Track the wrapper's pixel size so Pixi's canvas matches it.
  useEffect(() => {
    const el = wrapperRef.current;
    if (el === null) return;
    const ro = new ResizeObserver(() => {
      setSize({ width: el.clientWidth, height: el.clientHeight });
    });
    ro.observe(el);
    setSize({ width: el.clientWidth, height: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // Keep wheel/pinch gestures from leaking to the browser (page scroll, and
  // ctrl+wheel page zoom on trackpads). React attaches its `onWheel` listener as
  // passive, so `e.preventDefault()` inside the synthetic handler is ignored — we
  // must swallow the default on a native, non-passive listener. The map/log zoom
  // and scroll logic still lives in `onWheel`; this effect only cancels the
  // browser default for wheel events anywhere over the board surface.
  useEffect(() => {
    const el = wrapperRef.current;
    if (el === null) return;
    const swallow = (e: WheelEvent) => e.preventDefault();
    el.addEventListener("wheel", swallow, { passive: false });
    return () => el.removeEventListener("wheel", swallow);
  }, []);

  // The map's world-pixel extent (outer hex corners), used to keep the camera
  // from scrolling more than ~one hex radius past the edge.
  const mapBounds = useMemo<MapBounds | null>(
    () => (hexes.length > 0 ? mapPixelBounds(hexes.map((h) => h.coord), HEX_SIZE) : null),
    [hexes],
  );

  // Constrain a view so the map can't drift into a sea of dead background: at most
  // PAN_OVERSCROLL past each edge, centred when the map is smaller than the rect.
  const clampView = useCallback(
    (v: View): View => {
      if (mapBounds === null || layout === null) return v;
      const { map } = layout;
      const x = clampViewAxis(
        v.x, v.zoom, map.x, map.width,
        mapBounds.minX, mapBounds.minX + mapBounds.width, PAN_OVERSCROLL,
      );
      const y = clampViewAxis(
        v.y, v.zoom, map.y, map.height,
        mapBounds.minY, mapBounds.minY + mapBounds.height, PAN_OVERSCROLL,
      );
      return v.x === x && v.y === y ? v : { ...v, x, y };
    },
    [mapBounds, layout],
  );

  // Keep the view in bounds whenever the map rect changes (resize, panels
  // toggling) so a previously valid pan can't leave dead space exposed. A
  // converging render-phase setState: `clampView` returns the same reference when
  // already in bounds, so this no-ops once settled (React's "adjust state during
  // render" pattern), and it never fights an in-flight glide (whose frames are
  // already clamped).
  if (view !== null) {
    const clamped = clampView(view);
    if (clamped !== view) setView(clamped);
  }

  // Pick the opening camera the first time we know the viewport size: zoom in as
  // far as is comfortable while keeping all of the viewer's own units in frame,
  // falling back to the whole map when the viewer owns none (spectator/replay).
  // Computed during render (a guarded, converging setState — React's blessed
  // "initialize from props" pattern).
  if (layout !== null && layout.map.width > 0 && layout.map.height > 0 && view === null && hexes.length > 0) {
    const frameOwnUnits = ownUnits.length > 0;
    const bounds = mapPixelBounds(
      frameOwnUnits ? ownUnits.map((u) => u.coord) : hexes.map((h) => h.coord),
      HEX_SIZE,
    );
    setView(clampView(fitView(bounds, layout.map, frameOwnUnits ? INITIAL_MAX_ZOOM : MAX_ZOOM)));
  }

  // --- reveal animation: glide the view toward a target over CAMERA_GLIDE_MS ---
  const animRef = useRef<number | null>(null);

  const cancelGlide = useCallback(() => {
    if (animRef.current !== null) {
      cancelAnimationFrame(animRef.current);
      animRef.current = null;
    }
  }, []);

  // Drop any in-flight glide if this component unmounts.
  useEffect(() => cancelGlide, [cancelGlide]);

  // Smoothly interpolate from the current view to `target`. The starting view is
  // captured live via the setState updater so a glide always begins from wherever
  // the camera actually is (including mid-glide).
  const glideTo = useCallback(
    (rawTarget: View) => {
      cancelGlide();
      const target = clampView(rawTarget);
      setView((from) => {
        if (from === null) return target;
        const start = performance.now();
        const step = (now: number) => {
          const t = Math.min(1, (now - start) / CAMERA_GLIDE_MS);
          const e = easeInOutCubic(t);
          setView({
            x: from.x + (target.x - from.x) * e,
            y: from.y + (target.y - from.y) * e,
            zoom: from.zoom + (target.zoom - from.zoom) * e,
          });
          animRef.current = t < 1 ? requestAnimationFrame(step) : null;
        };
        animRef.current = requestAnimationFrame(step);
        return from;
      });
    },
    [cancelGlide, clampView],
  );

  // Roster row → bring the unit into view: do nothing if it's already comfortably
  // framed, otherwise glide (at the current zoom) until it's centred.
  const revealUnit = useCallback(
    (coord: Axial) => {
      if (view === null || layout === null) return;
      const { map } = layout;
      const p = axialToPixel(coord.q, coord.r, HEX_SIZE);
      const sx = view.x + p.x * view.zoom;
      const sy = view.y + p.y * view.zoom;
      const marginX = map.width * COMFORT_MARGIN;
      const marginY = map.height * COMFORT_MARGIN;
      const comfortable =
        sx >= map.x + marginX &&
        sx <= map.x + map.width - marginX &&
        sy >= map.y + marginY &&
        sy <= map.y + map.height - marginY;
      if (comfortable) return;
      glideTo({
        zoom: view.zoom,
        x: map.x + map.width / 2 - p.x * view.zoom,
        y: map.y + map.height / 2 - p.y * view.zoom,
      });
    },
    [view, layout, glideTo],
  );

  // --- input: drag-pan, wheel-zoom, click-to-select (all DOM-level) ---
  const dragRef = useRef<{ startX: number; startY: number; vx: number; vy: number; moved: boolean } | null>(null);
  // A pointer press that began over the roster — resolved on pointer-up as a row
  // click, never as a map pan.
  const rosterPressRef = useRef(false);
  // A pointer press that began over the bottom dock (palette / lock) — resolved on
  // pointer-up as a button click, never as a map pan.
  const bottomPressRef = useRef(false);

  /** Canvas-space (sx, sy) for a client point, relative to the wrapper. */
  const localPoint = useCallback((clientX: number, clientY: number): { sx: number; sy: number } => {
    const rect = wrapperRef.current?.getBoundingClientRect();
    return { sx: clientX - (rect?.left ?? 0), sy: clientY - (rect?.top ?? 0) };
  }, []);

  const screenToWorld = useCallback(
    (clientX: number, clientY: number, v: View): { x: number; y: number } => {
      const { sx, sy } = localPoint(clientX, clientY);
      return { x: (sx - v.x) / v.zoom, y: (sy - v.y) / v.zoom };
    },
    [localPoint],
  );

  /** Whether a canvas point is over the docked roster panel. */
  const overRoster = useCallback(
    (sx: number, sy: number): boolean =>
      showPanels && layout !== null && pointInRect(sx, sy, layout.roster),
    [showPanels, layout],
  );

  /** Whether a canvas point is over the top-right hex inspector (planning only —
   *  the rect is empty in replay). It floats over the map's corner, so pointer
   *  input there must not pan, hover, or click the hexes beneath it. */
  const overInspector = useCallback(
    (sx: number, sy: number): boolean =>
      showPanels && layout !== null && pointInRect(sx, sy, layout.inspector),
    [showPanels, layout],
  );

  /** Whether a canvas point is over the bottom dock (controls/lock or replay log). */
  const overBottom = useCallback(
    (sx: number, sy: number): boolean =>
      showPanels &&
      layout !== null &&
      (pointInRect(sx, sy, layout.selectedUnit) ||
        pointInRect(sx, sy, layout.lock) ||
        pointInRect(sx, sy, layout.replayLog)),
    [showPanels, layout],
  );

  /** The palette button under a canvas point, when the palette is live (a unit
   *  selected, planning interactive, not replay) — feeds hover/pressed art. */
  const paletteActionAt = useCallback(
    (sx: number, sy: number): PaletteAction | null => {
      if (layout === null || planning === undefined || locked) return null;
      if (replayControls !== undefined) return null;
      if (planning.selectedCity !== null || planning.selectedUnit === null) return null;
      if (!pointInRect(sx, sy, layout.selectedUnit)) return null;
      return paletteButtonAt(
        sx - layout.selectedUnit.x,
        sy - layout.selectedUnit.y,
        planning.selectedUnit,
      );
    },
    [layout, planning, locked, replayControls],
  );

  /** The replay-control button under a canvas point during replay — feeds
   *  the keycaps' pressed art. */
  const replayActionAt = useCallback(
    (sx: number, sy: number): ReplayAction | null => {
      if (layout === null || replayControls === undefined) return null;
      if (!pointInRect(sx, sy, layout.selectedUnit)) return null;
      const atEnd = replayControls.currentBeat >= replayControls.beats.length - 1;
      return replayButtonAt(
        sx - layout.selectedUnit.x,
        sy - layout.selectedUnit.y,
        layout.selectedUnit.width,
        atEnd,
      );
    },
    [layout, replayControls],
  );

  /** Run a palette button's action against the selected unit (UI-SPEC §8). */
  const runPaletteAction = useCallback(
    (action: PaletteAction, unitId: number) => {
      if (planning === undefined) return;
      switch (action) {
        case "move":
          planning.enterMove(unitId);
          break;
        case "barrage":
          planning.enterBarrage(unitId);
          break;
        case "rest":
          planning.setBareOrder(unitId, "rest");
          break;
        case "hold":
          planning.setBareOrder(unitId, "hold");
          break;
        case "clear":
          planning.clearOrder(unitId);
          break;
      }
    },
    [planning],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (view === null) return;
      const { sx, sy } = localPoint(e.clientX, e.clientY);
      if (overRoster(sx, sy)) {
        // Press over the roster: handled on pointer-up, no pan/capture.
        rosterPressRef.current = true;
        bottomPressRef.current = false;
        return;
      }
      if (overBottom(sx, sy)) {
        // Press over the bottom dock: handled on pointer-up, no pan/capture.
        // The held palette button (if any) shows its pressed art meanwhile.
        bottomPressRef.current = true;
        rosterPressRef.current = false;
        setPalettePressed(paletteActionAt(sx, sy));
        setReplayPressed(replayActionAt(sx, sy));
        return;
      }
      if (overInspector(sx, sy)) {
        // Press over the inspector: inert (it has no buttons), but it must not
        // fall through to a pan of the map beneath it.
        rosterPressRef.current = false;
        bottomPressRef.current = false;
        return;
      }
      rosterPressRef.current = false;
      bottomPressRef.current = false;
      cancelGlide(); // a manual grab takes over from any in-flight reveal
      e.currentTarget.setPointerCapture(e.pointerId);
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        vx: view.x,
        vy: view.y,
        moved: false,
      };
    },
    [view, localPoint, overRoster, overBottom, overInspector, cancelGlide, paletteActionAt, replayActionAt],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const drag = dragRef.current;
      if (drag !== null) {
        const dx = e.clientX - drag.startX;
        const dy = e.clientY - drag.startY;
        if (Math.abs(dx) > DRAG_THRESHOLD_PX || Math.abs(dy) > DRAG_THRESHOLD_PX) {
          drag.moved = true;
        }
        setView((v) => (v === null ? v : clampView({ ...v, x: drag.vx + dx, y: drag.vy + dy })));
        return;
      }
      // Hovering the roster: highlight the row under the pointer (no map preview).
      const { sx, sy } = localPoint(e.clientX, e.clientY);
      if (overRoster(sx, sy) && layout !== null) {
        const idx = rosterRowAt(sy - layout.roster.y, rows.length);
        setRosterHover((prev) => (prev === idx ? prev : idx));
        setHoverKey(null);
        setInspectKey(null);
        return;
      }
      setRosterHover((prev) => (prev === null ? prev : null));
      // Over the bottom dock or the inspector itself: no map hover of any kind
      // (but the palette tracks its own button hover for the chrome art).
      if (overBottom(sx, sy) || overInspector(sx, sy)) {
        const action = paletteActionAt(sx, sy);
        setPaletteHover((prev) => (prev === action ? prev : action));
        setHoverKey((prev) => (prev === null ? prev : null));
        setInspectKey((prev) => (prev === null ? prev : null));
        return;
      }
      setPaletteHover((prev) => (prev === null ? prev : null));
      if (planning === undefined || view === null) return;
      const world = screenToWorld(e.clientX, e.clientY, view);
      const ax = pixelToAxial(world.x, world.y, HEX_SIZE);
      const key = hexKey(ax.q, ax.r);
      const onMap = hexByKey.has(key);
      // Inspector hover (UI-SPEC §7): tracked in every planning state — the
      // locked, read-only board still describes the hex under the cursor.
      const nextInspect = onMap ? key : null;
      setInspectKey((prev) => (prev === nextInspect ? prev : nextInspect));
      // Route-preview hover: only while planning is interactive. With a unit
      // selected (move mode only — not while aiming a barrage) it feeds the
      // route preview; with nothing selected it lets hovering a unit reveal
      // that unit's pulse badges. Locked plans track nothing.
      if (locked) return;
      if (selectedUnitId !== null && mode !== "move") return;
      const next = onMap ? key : null;
      setHoverKey((prev) => (prev === next ? prev : next));
    },
    [planning, locked, selectedUnitId, mode, view, screenToWorld, hexByKey, localPoint, overRoster, overBottom, overInspector, layout, rows.length, clampView, paletteActionAt],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      setPalettePressed(null);
      setReplayPressed(null);
      // A press that started on the roster: select the clicked row's unit + jump.
      if (rosterPressRef.current) {
        rosterPressRef.current = false;
        const { sx, sy } = localPoint(e.clientX, e.clientY);
        if (planning !== undefined && layout !== null && overRoster(sx, sy)) {
          const idx = rosterRowAt(sy - layout.roster.y, rows.length);
          if (idx !== null) {
            const row = rows[idx];
            planning.selectUnit(row.unit.id);
            revealUnit(row.unit.coord);
          }
        }
        return;
      }

      // A press that started on the bottom dock: route to a palette / lock /
      // replay-control button.
      if (bottomPressRef.current) {
        bottomPressRef.current = false;
        const { sx, sy } = localPoint(e.clientX, e.clientY);
        if (layout === null) return;

        // Replay mode: route to the replay control strip.
        if (replayControls !== undefined && pointInRect(sx, sy, layout.selectedUnit)) {
          const localX = sx - layout.selectedUnit.x;
          const localY = sy - layout.selectedUnit.y;
          const atEnd = replayControls.currentBeat >= replayControls.beats.length - 1;
          const action = replayButtonAt(localX, localY, layout.selectedUnit.width, atEnd);
          if (action !== null) {
            switch (action) {
              case "rewind": replayControls.onRewind(); break;
              case "back": replayControls.onBack(); break;
              case "play_pause": replayControls.onPlayPause(); break;
              case "forward": replayControls.onForward(); break;
              case "skip": replayControls.onSkip(); break;
              case "speed": replayControls.onToggleSpeed(); break;
              case "start_planning": replayControls.onStartPlanning(); break;
            }
          }
          return;
        }

        // Replay mode: a click in the event log pauses + jumps to that beat.
        if (
          replayLog !== undefined &&
          logMeasure !== null &&
          pointInRect(sx, sy, layout.replayLog)
        ) {
          const beatIndex = beatIndexAtY(logMeasure, sy - layout.replayLog.y, logScroll);
          if (beatIndex !== null) replayLog.onJumpToBeat(beatIndex);
          return;
        }

        if (planning === undefined) return;
        // Locked: the dock is read-only — its buttons are greyed and inert, but a
        // press here still must not fall through to a map pan.
        if (locked) return;
        if (pointInRect(sx, sy, layout.selectedUnit)) {
          const localX = sx - layout.selectedUnit.x;
          const localY = sy - layout.selectedUnit.y;
          const city = planning.selectedCity;
          if (city !== null) {
            // Recruit panel: a type button stages it (or toggles it off if already
            // staged here); affordability/cap are gated against the running plan.
            const type = recruitButtonAt(localX, localY);
            if (type !== null) {
              const avail = recruitAvailability(
                city,
                planning.recruitBySite,
                bank,
                ownUnits.length,
                unitCap,
              );
              if (avail.currentType === type) {
                planning.clearRecruit(city);
              } else {
                const button = recruitButtons().find((b) => b.type === type);
                if (button !== undefined && canRecruit(button, avail)) {
                  planning.stageRecruit(city, type);
                }
              }
            }
          } else {
            const unit = planning.selectedUnit;
            if (unit !== null) {
              const action = paletteButtonAt(localX, localY, unit);
              if (action !== null) runPaletteAction(action, unit.id);
            }
          }
        } else if (lock !== undefined && !lock.submitting && pointInRect(sx, sy, layout.lock)) {
          lock.onLock();
        }
        return;
      }

      const drag = dragRef.current;
      dragRef.current = null;
      if (drag === null || view === null) return;
      if (drag.moved) return;

      const world = screenToWorld(e.clientX, e.clientY, view);
      const ax = pixelToAxial(world.x, world.y, HEX_SIZE);
      const onMap = hexByKey.has(hexKey(ax.q, ax.r));
      if (planning !== undefined) {
        if (onMap) {
          if (locked) {
            // Locked: clicks only review-select one of your own units (no order
            // edits) — clicking elsewhere clears the selection.
            const own = ownUnits.find((u) => axialEqual(u.coord, ax));
            planning.selectUnit(own?.id ?? null);
          } else {
            // Planning: clicks select units and build paths (off-map is ignored).
            planning.onHexClick(ax);
          }
        }
        // The committed path may have moved; recompute the preview on the next move.
        setHoverKey(null);
      } else {
        // Inspect mode: clicks surface hex info.
        setSelected(onMap ? ax : null);
      }
    },
    [view, screenToWorld, hexByKey, planning, locked, lock, localPoint, overRoster, layout, rows, revealUnit, runPaletteAction, bank, unitCap, ownUnits, replayControls, replayLog, logMeasure, logScroll],
  );

  const onPointerLeave = useCallback(() => {
    dragRef.current = null;
    rosterPressRef.current = false;
    bottomPressRef.current = false;
    setHoverKey(null);
    setInspectKey(null);
    setRosterHover(null);
  }, []);

  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      const { sx, sy } = localPoint(e.clientX, e.clientY);
      // Wheel over the replay log scrolls it (rather than zooming the map).
      if (
        replayLog !== undefined &&
        logMeasure !== null &&
        layout !== null &&
        pointInRect(sx, sy, layout.replayLog)
      ) {
        const limit = maxScroll(logMeasure.contentHeight, logViewportHeight(layout.replayLog.height));
        setLogScroll((s) => Math.max(0, Math.min(limit, s + e.deltaY)));
        return;
      }
      if (overRoster(sx, sy) || overBottom(sx, sy) || overInspector(sx, sy)) return; // panels don't zoom the map
      cancelGlide(); // a manual zoom takes over from any in-flight reveal
      setView((v) => {
        if (v === null) return v;
        const factor = Math.exp(-e.deltaY * 0.0015);
        const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, v.zoom * factor));
        // keep the point under the cursor fixed while zooming
        const worldX = (sx - v.x) / v.zoom;
        const worldY = (sy - v.y) / v.zoom;
        return clampView({ zoom, x: sx - worldX * zoom, y: sy - worldY * zoom });
      });
    },
    [localPoint, overRoster, overBottom, overInspector, cancelGlide, clampView, replayLog, logMeasure, layout],
  );

  // --- draw callbacks ---
  // Base terrain fill: flat colours that show until the sprite textures load, and
  // remain the fallback if loading fails. The sprite layer (when present) draws on
  // top of this; the overlay (grid/roads/rings/fog) draws on top of the sprites.
  const drawTerrainBase = useCallback(
    (g: Graphics) => {
      g.clear();
      for (const h of hexes) {
        const { x, y } = axialToPixel(h.coord.q, h.coord.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE));
        g.fill({ color: TERRAIN_FILL[h.terrain] });
      }
    },
    [hexes],
  );

  // Terrain overlay drawn above the tile sprites: the hex grid outline, the road
  // network, settlement ownership rings, and the fog-of-war veil.
  const drawTerrainOverlay = useCallback(
    (g: Graphics) => {
      g.clear();

      // hex grid outline
      for (const h of hexes) {
        const { x, y } = axialToPixel(h.coord.q, h.coord.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE));
      }
      g.stroke({ width: 2, color: HEX_STROKE });

      // road network: link road/bridge hexes centre-to-edge toward road neighbours
      for (const h of hexes) {
        if (!h.road && h.terrain !== "bridge") continue;
        // Painterly (128px) bridge tiles bake their own road ramps, which stop
        // at the deck face — vector stubs here would stripe brown across the
        // deck. Legacy 64px bridge art has no ramps and still needs the stubs.
        if (h.terrain === "bridge" && terrainTex !== null) {
          const oriented = orientedRiverTile(
            riverCourse.get(hexKey(h.coord.q, h.coord.r)) ?? [],
          );
          const variants = oriented
            ? terrainTex.byOriented[`bridge/${oriented.name}`]
            : undefined;
          if (variants !== undefined && variants[0].source.pixelWidth > 64) continue;
        }
        // Hexes rendering a baked road tile (T5) carry their road in the art;
        // vector stubs would double-draw across it. Their neighbours still draw
        // half-stubs toward this hex, which meet the baked art's edge-midpoint
        // crossing (same width/colour by construction).
        if (h.road && terrainTex !== null) {
          const ref = roadTiles.get(hexKey(h.coord.q, h.coord.r));
          if (ref !== undefined && terrainTex.byOriented[ref.lookup] !== undefined) {
            continue;
          }
        }
        const c = axialToPixel(h.coord.q, h.coord.r, HEX_SIZE);
        for (const d of AXIAL_DIRECTIONS) {
          const n = hexByKey.get(hexKey(h.coord.q + d.q, h.coord.r + d.r));
          if (n === undefined) continue;
          if (!n.road && n.terrain !== "bridge") continue;
          const nc = axialToPixel(n.coord.q, n.coord.r, HEX_SIZE);
          g.moveTo(c.x, c.y);
          g.lineTo((c.x + nc.x) / 2, (c.y + nc.y) / 2);
        }
      }
      g.stroke({ width: 6, color: 0x6b4f2a });

      // settlement ownership rings
      for (const [key, owner] of settlementOwner) {
        const hex = hexByKey.get(key);
        if (hex === undefined) continue;
        const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE - 3));
        g.stroke({
          width: 6,
          color: owner === null ? NEUTRAL_COLOR : playerColor(owner),
        });
      }

      // fog of war: veil every hex outside the viewer's vision last, so the wash
      // dims terrain, roads, and settlement rings alike (the map is public, but
      // unseen ground should read as out-of-sight).
      if (visibleKeys !== null) {
        for (const h of hexes) {
          if (visibleKeys.has(hexKey(h.coord.q, h.coord.r))) continue;
          const { x, y } = axialToPixel(h.coord.q, h.coord.r, HEX_SIZE);
          g.poly(hexCornerPoints(x, y, HEX_SIZE));
          g.fill({ color: FOG_COLOR, alpha: FOG_ALPHA });
        }
      }
    },
    [hexes, hexByKey, settlementOwner, visibleKeys, riverCourse, roadTiles, terrainTex],
  );

  const drawSelection = useCallback(
    (g: Graphics) => {
      g.clear();
      if (selected === null) return;
      const { x, y } = axialToPixel(selected.q, selected.r, HEX_SIZE);
      g.poly(hexCornerPoints(x, y, HEX_SIZE));
      g.stroke({ width: 5, color: 0xffffff });
    },
    [selected],
  );

  // Advisory layer (SPEC §16.1), drawn under the planning paths: an amber ✕ on
  // each friendly traffic jam. (Enemy ZOC is no longer a hex halo — it surfaces
  // as the planned path churning red; see the staged-order draw below.)
  const drawTraffic = useCallback(
    (g: Graphics) => {
      g.clear();
      if (advisory === undefined) return;

      for (const key of advisory.trafficHexes) {
        const hex = hexByKey.get(key);
        if (hex === undefined) continue;
        const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
        g.moveTo(x - 16, y - 16).lineTo(x + 16, y + 16);
        g.moveTo(x + 16, y - 16).lineTo(x - 16, y + 16);
      }
      g.stroke({ width: 5, color: TRAFFIC_COLOR });
    },
    [advisory, hexByKey],
  );

  // Planning layer drawn UNDER the unit sprites: the selected unit's
  // movement/barrage range outlines and the selection rings. The staged order
  // arrows and hover preview draw in `drawOrders`, above the sprites.
  const drawPlanning = useCallback(
    (g: Graphics) => {
      g.clear();
      if (planning === undefined) return;

      // movement range: outline each reachable hex (no fill)
      if (continuations !== null) {
        for (const key of continuations.keys()) {
          const hex = hexByKey.get(key);
          if (hex === undefined) continue;
          const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
          g.poly(hexCornerPoints(x, y, HEX_SIZE - 3));
        }
        g.stroke({ width: 3, color: RANGE_OUTLINE, alpha: 0.6 });
      }

      // barrage range: outline every hex the selected artillery may target
      if (planning.barrageTargetKeys.size > 0) {
        for (const key of planning.barrageTargetKeys) {
          const hex = hexByKey.get(key);
          if (hex === undefined) continue;
          const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
          g.poly(hexCornerPoints(x, y, HEX_SIZE - 3));
        }
        g.stroke({ width: 3, color: BARRAGE_RANGE, alpha: 0.6 });
      }

      // ring on the selected unit
      if (selectedUnit !== null) {
        const { x, y } = axialToPixel(selectedUnit.coord.q, selectedUnit.coord.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE));
        g.stroke({ width: 5, color: 0xffffff });
      }

      // ring on the selected recruit city (same emphasis as a selected unit)
      if (planning.selectedCity !== null) {
        const { x, y } = axialToPixel(planning.selectedCity.q, planning.selectedCity.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE));
        g.stroke({ width: 5, color: 0xffffff });
      }
    },
    [planning, continuations, hexByKey, selectedUnit],
  );

  // Order layer drawn ABOVE the unit sprites: every staged move as a tapered
  // ribbon arrow (selected heavier), staged barrages, and the hover route
  // preview — all rimmed with a translucent white casing for legibility over
  // sprites and bright terrain.
  const drawOrders = useCallback(
    (g: Graphics) => {
      g.clear();
      if (planning === undefined) return;

      const unitById = new Map(units.map((u) => [u.id, u] as const));

      // One ribbon: translucent white casing (fill + round-join stroke — the
      // colored ribbon covers all but the outer rim, so the rim composites at
      // exactly the casing alpha), then the colored pieces on top.
      const ribbon = (
        centers: { x: number; y: number }[],
        stopSteps: number,
        dims: ArrowDims,
        color: number,
        blockedColor: number,
        casingAlpha: number,
      ) => {
        const { casing, reachable, blocked } = moveArrowOutlines(centers, stopSteps, dims);
        if (casing.length === 0) return;
        g.poly(casing);
        g.fill({ color: ARROW_CASING, alpha: casingAlpha });
        g.stroke({
          width: ARROW_EDGE + 2 * ARROW_CASING_RIM,
          color: ARROW_CASING,
          alpha: casingAlpha,
          join: "round",
          cap: "round",
        });
        if (blocked !== null) {
          g.poly(blocked);
          g.fill({ color: blockedColor });
          g.stroke({ width: ARROW_EDGE, color: blockedColor, join: "round", cap: "round" });
        }
        if (reachable !== null) {
          g.poly(reachable);
          g.fill({ color });
          g.stroke({ width: ARROW_EDGE, color, join: "round", cap: "round" });
        }
      };

      for (const [idStr, order] of Object.entries(planning.stagedByUnit)) {
        const unit = unitById.get(Number(idStr));
        if (unit === undefined) continue;
        const active = Number(idStr) === planning.selectedUnitId;
        const origin = axialToPixel(unit.coord.q, unit.coord.r, HEX_SIZE);

        if (order.kind === "move") {
          if (order.path.length === 0) continue;
          // Where enemy contact is expected to impede progress (SPEC §8.4): the
          // unit halts *before* this hex, so the ribbon churns red from there.
          // Absent a block, `stopSteps` is the path length (all gold).
          const stop = advisory?.zocStopByUnit.get(Number(idStr));
          const stopSteps =
            stop === undefined
              ? order.path.length
              : order.path.findIndex((p) => axialEqual(p, stop));
          const centers = [unit.coord, ...order.path].map((c) =>
            axialToPixel(c.q, c.r, HEX_SIZE),
          );
          ribbon(
            centers,
            stopSteps,
            active ? ARROW_DIMS : ARROW_DIMS_STAGED,
            active ? PATH_COLOR : PATH_STAGED_COLOR,
            active ? PATH_BLOCKED_COLOR : PATH_BLOCKED_STAGED_COLOR,
            active ? ARROW_CASING_ALPHA : ARROW_CASING_ALPHA_STAGED,
          );
        } else if (order.kind === "barrage") {
          // a cased line from the gun to its target + a reticle on the target
          const t = axialToPixel(order.target.q, order.target.r, HEX_SIZE);
          const d = Math.hypot(t.x - origin.x, t.y - origin.y);
          const sx = origin.x + ((t.x - origin.x) / d) * ARROW_DIMS.startOffset;
          const sy = origin.y + ((t.y - origin.y) / d) * ARROW_DIMS.startOffset;
          const casingAlpha = active ? ARROW_CASING_ALPHA : ARROW_CASING_ALPHA_STAGED;
          g.moveTo(sx, sy).lineTo(t.x, t.y);
          g.stroke({
            width: 4 + 2 * ARROW_CASING_RIM,
            color: ARROW_CASING,
            alpha: casingAlpha,
            cap: "round",
          });
          g.moveTo(sx, sy).lineTo(t.x, t.y);
          g.stroke({ width: 4, color: BARRAGE_COLOR, alpha: active ? 1 : 0.85, cap: "round" });
          g.circle(t.x, t.y, 12);
          g.stroke({ width: 4, color: BARRAGE_COLOR });
          g.moveTo(t.x - 18, t.y).lineTo(t.x + 18, t.y);
          g.moveTo(t.x, t.y - 18).lineTo(t.x, t.y + 18);
          g.stroke({ width: 3, color: BARRAGE_COLOR });
        }
        // rest/hold carry no board geometry — they render as a text badge below.
      }

      // hovered route preview: the segment a click would append, from the
      // selected unit's current path end through the previewed hexes
      if (selectedUnit !== null && previewPath !== null) {
        const segment = previewPath.slice(selectedPath.length);
        if (segment.length > 0) {
          const from =
            selectedPath.length > 0
              ? selectedPath[selectedPath.length - 1]
              : selectedUnit.coord;
          const centers = [from, ...segment].map((c) => axialToPixel(c.q, c.r, HEX_SIZE));
          // Off a bare unit the preview lifts clear of the sprite like a real
          // arrow; extending a committed path it hugs the path's end instead.
          const dims =
            selectedPath.length > 0
              ? ARROW_DIMS_PREVIEW
              : { ...ARROW_DIMS_PREVIEW, startOffset: ARROW_DIMS.startOffset };
          const { casing } = moveArrowOutlines(centers, segment.length, dims);
          if (casing.length > 0) {
            g.poly(casing);
            g.fill({ color: ARROW_CASING, alpha: 0.3 });
            g.stroke({
              width: ARROW_EDGE + 2 * ARROW_CASING_RIM,
              color: ARROW_CASING,
              alpha: 0.3,
              join: "round",
              cap: "round",
            });
            g.poly(casing);
            g.fill({ color: PREVIEW_COLOR });
            g.stroke({ width: ARROW_EDGE, color: PREVIEW_COLOR, join: "round", cap: "round" });
          }
        }
      }
    },
    [planning, advisory, units, selectedUnit, selectedPath, previewPath],
  );

  // Pulse badges ride the arrow of exactly one unit — the selected one, or,
  // with nothing selected, the unit under the cursor. Never all arrows at
  // once: crossing paths would land clashing numbers on shared hexes.
  const pulseBadges = useMemo<{ coord: Axial; n: number; blocked: boolean }[]>(() => {
    if (planning === undefined) return [];
    let unitId = planning.selectedUnitId;
    // Hover tracking stops once locked (onPointerMove), so a hoverKey from just
    // before the lock is stale — ignore it rather than freezing badges on.
    if (unitId === null && hoverKey !== null && !locked) {
      const hovered = units.find((u) => hexKey(u.coord.q, u.coord.r) === hoverKey);
      unitId = hovered?.id ?? null;
    }
    if (unitId === null) return [];
    const order = planning.stagedByUnit[unitId];
    if (order === undefined || order.kind !== "move" || order.path.length === 0) return [];
    const stop = advisory?.zocStopByUnit.get(unitId);
    const stopSteps =
      stop === undefined ? order.path.length : order.path.findIndex((p) => axialEqual(p, stop));
    return order.path.map((coord, i) => ({ coord, n: i + 1, blocked: i >= stopSteps }));
  }, [planning, hoverKey, locked, units, advisory]);

  // The selected unit's staged order + its §5.2 display label, for the detail panel.
  const selectedStaged =
    selectedUnitId === null ? undefined : planning?.stagedByUnit[selectedUnitId];
  const selectedOrderLabel = orderLabelOf(selectedStaged, enemyHexKeys);

  // The hex the top-right inspector describes (UI-SPEC §7): the hovered hex,
  // falling back to the selected recruit city's, then the selected unit's;
  // null renders the hover hint instead.
  const inspectHex = useMemo<Hex | null>(() => {
    if (planning === undefined) return null;
    if (inspectKey !== null) return hexByKey.get(inspectKey) ?? null;
    if (selectedCity !== null) {
      return hexByKey.get(hexKey(selectedCity.q, selectedCity.r)) ?? null;
    }
    if (selectedUnit !== null) {
      return hexByKey.get(hexKey(selectedUnit.coord.q, selectedUnit.coord.r)) ?? null;
    }
    return null;
  }, [planning, inspectKey, selectedCity, selectedUnit, hexByKey]);
  const inspectSettlement = useMemo<InspectedSettlement | null>(() => {
    if (inspectHex === null) return null;
    const key = hexKey(inspectHex.coord.q, inspectHex.coord.r);
    if (!settlementOwner.has(key)) return null;
    return { owner: settlementOwner.get(key) ?? null };
  }, [inspectHex, settlementOwner]);
  // `units` is already fog-filtered server-side, so any occupant found here is
  // visible to the viewer (UI-SPEC §9: known-unit info only within vision).
  const inspectUnit = useMemo<Unit | null>(() => {
    if (inspectHex === null) return null;
    return units.find((u) => axialEqual(u.coord, inspectHex.coord)) ?? null;
  }, [inspectHex, units]);

  // Zoom LOD (unitHud.ts): below the threshold the per-unit HUD (health bar,
  // order badge, reserved status slot) hides and units collapse to sprites.
  const hudVisible = view !== null && hudVisibleAtZoom(view.zoom);

  return (
    <div
      ref={wrapperRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerLeave}
      onWheel={onWheel}
      className="relative h-full w-full cursor-grab touch-none overflow-hidden bg-zinc-950 active:cursor-grabbing"
    >
      {size !== null && view !== null && (
        <Application
          resizeTo={wrapperRef}
          backgroundColor={BACKGROUND}
          antialias
          autoDensity
        >
          <pixiContainer x={view.x} y={view.y} scale={view.zoom}>
            <pixiGraphics draw={drawTerrainBase} />
            {terrainTex !== null &&
              hexes.map((h) => {
                const { x, y } = axialToPixel(h.coord.q, h.coord.r, HEX_SIZE);
                // Tiles span two generations (64px legacy, 128px painterly), so
                // scale by the texture's own size to land at HEX_SIZE*2 world px.
                const { tex, mirror } = hexTileRef(h, terrainTex, riverCourse, roadTiles);
                const s = (HEX_SIZE * 2) / tex.width;
                return (
                  <pixiSprite
                    key={hexKey(h.coord.q, h.coord.r)}
                    texture={tex}
                    x={x}
                    y={y}
                    anchor={0.5}
                    scale={{ x: mirror ? -s : s, y: s }}
                  />
                );
              })}
            <pixiGraphics draw={drawTerrainOverlay} />
            <pixiGraphics draw={drawSelection} />
            <pixiGraphics draw={drawTraffic} />
            <pixiGraphics draw={drawPlanning} />
            {units.map((u) => (
              <UnitMarker
                key={u.id}
                unit={u}
                tex={unitTex}
                hud={hudVisible}
                posOverride={replayAnim?.slideOverrides.get(u.id)}
                alpha={replayAnim?.fadingOut.get(u.id) ?? 1}
              />
            ))}
            {/* Recruited units fading in — not yet in `state.units`. */}
            {replayAnim?.fadingIn &&
              Array.from(replayAnim.fadingIn.values()).map(({ unit, alpha }) => (
                <UnitMarker
                  key={`recruit-${unit.id}`}
                  unit={unit}
                  tex={unitTex}
                  hud={hudVisible}
                  alpha={alpha}
                />
              ))}
            <pixiGraphics draw={drawOrders} />
            {pulseBadges.map(({ coord, n, blocked }) => (
              <PulseBadge key={`pulse-${n}`} coord={coord} n={n} blocked={blocked} />
            ))}
            {planning !== undefined &&
              hudVisible &&
              units.map((u) => {
                const order = planning.stagedByUnit[u.id];
                if (order === undefined || (order.kind !== "rest" && order.kind !== "hold"))
                  return null;
                return <OrderBadge key={`badge-${u.id}`} unit={u} label={order.kind} />;
              })}
            {planning !== undefined &&
              Object.values(planning.recruitBySite).map((r) => (
                <RecruitGhost
                  key={`recruit-${r.coord.q}-${r.coord.r}`}
                  coord={r.coord}
                  type={r.type}
                  owner={yourSlot}
                  facing={defaultFacing(r.coord, yourSlot, units)}
                  tex={unitTex}
                />
              ))}
          </pixiContainer>
          {showPanels && layout !== null && (
            <>
              <RosterPanel
                rect={layout.roster}
                rows={rows}
                hoverIndex={rosterHover}
                chrome={chromeTex}
              />
              {planning !== undefined && (
                <InspectorPanel
                  rect={layout.inspector}
                  hex={inspectHex}
                  settlement={inspectSettlement}
                  unit={inspectUnit}
                  chrome={chromeTex}
                />
              )}
              {replayControls !== undefined ? (
                <ReplayControlPanel
                  rect={layout.selectedUnit}
                  chrome={chromeTex}
                  pressedAction={replayPressed}
                  {...replayControls}
                />
              ) : selectedCity !== null && planning !== undefined ? (
                <RecruitPanel
                  rect={layout.selectedUnit}
                  city={selectedCity}
                  owner={yourSlot}
                  recruitBySite={planning.recruitBySite}
                  bank={bank}
                  ownUnitCount={ownUnits.length}
                  unitCap={unitCap}
                  locked={locked}
                />
              ) : (
                <SelectedUnitPanel
                  rect={layout.selectedUnit}
                  unit={planning?.selectedUnit ?? null}
                  tex={unitTex}
                  tile={selectedUnitTile}
                  chrome={chromeTex}
                  staged={selectedStaged}
                  mode={mode}
                  orderLabel={selectedOrderLabel}
                  warnings={
                    selectedUnitId === null ? EMPTY_WARNINGS : (advisory?.byUnit.get(selectedUnitId) ?? EMPTY_WARNINGS)
                  }
                  locked={locked}
                  hoverAction={paletteHover}
                  pressedAction={palettePressed}
                />
              )}
              {lock !== undefined && (
                <LockPanel
                  rect={layout.lock}
                  chrome={chromeTex}
                  ordered={lock.ordered}
                  total={lock.total}
                  idle={lock.idle}
                  submitting={lock.submitting}
                  error={lock.error}
                  locked={lock.locked}
                />
              )}
              {replayLog !== undefined && logMeasure !== null && (
                <ReplayLogPanel
                  rect={layout.replayLog}
                  chrome={chromeTex}
                  logBeats={replayLog.logBeats}
                  measure={logMeasure}
                  currentBeat={currentBeat}
                  scroll={logScroll}
                />
              )}
            </>
          )}
          {gameOver !== undefined && size !== null && (
            <GameOverPanel
              rect={{
                x: size.width - LOCK_WIDTH,
                y: size.height - BOTTOM_HEIGHT,
                width: LOCK_WIDTH,
                height: BOTTOM_HEIGHT,
              }}
              outcome={gameOver.outcome}
              reason={gameOver.reason}
            />
          )}
        </Application>
      )}
      {planning === undefined && selected !== null && (
        <HexInfo state={state} selected={selected} />
      )}
    </div>
  );
}

/**
 * The texture + mirror the board draws for a hex, in branch priority order:
 * river/bridge oriented art (chosen by edge mask so the water/deck connects
 * across shared edges, possibly L↔R mirrored, unhandled shapes falling back to
 * the generic "00" tile), baked road-on-terrain art (T5 — a missing texture
 * falls through to the base tile + vector road overlay), then the coord-hashed
 * cosmetic variant of the base terrain. Shared by the map render loop and the
 * dock portrait so the portrait backdrop always matches the board.
 */
function hexTileRef(
  h: Hex,
  terrainTex: LoadedTiles,
  riverCourse: Map<string, number[]>,
  roadTiles: Map<string, RoadTileRef>,
): PortraitTile {
  if (h.terrain === "river" || h.terrain === "bridge") {
    const oriented = orientedRiverTile(
      riverCourse.get(hexKey(h.coord.q, h.coord.r)) ?? [],
    );
    const name = oriented ? `${h.terrain}/${oriented.name}` : `${h.terrain}/00`;
    const variants =
      terrainTex.byOriented[name] ?? terrainTex.byOriented[`${h.terrain}/00`];
    const tex = variants[variantIndex(h.coord.q, h.coord.r, variants.length)];
    return { tex, mirror: oriented?.mirror ?? false };
  }
  const roadRef = h.road ? roadTiles.get(hexKey(h.coord.q, h.coord.r)) : undefined;
  const roadVariants =
    roadRef === undefined ? undefined : terrainTex.byOriented[roadRef.lookup];
  if (roadRef !== undefined && roadVariants !== undefined) {
    const tex = roadVariants[variantIndex(h.coord.q, h.coord.r, roadVariants.length)];
    return { tex, mirror: roadRef.mirror };
  }
  const variants = terrainTex.byTerrain[h.terrain];
  return {
    tex: variants[variantIndex(h.coord.q, h.coord.r, variants.length)],
    mirror: false,
  };
}

/**
 * A single unit: two-layer painterly sprite (neutral base + owner-tinted team
 * mask, see `units.ts`) with its HUD on the fixed slots from `unitHud.ts` —
 * a 10-pip HP bar above (damaged units only), rest/hold badge below (rendered
 * by the board), reserved status stack top-right. `hud` is the zoom-LOD gate:
 * false collapses the unit to just its sprite. Until the sprite textures
 * load, falls back to the original vector marker (shape encodes type) + hp
 * text, mirroring the terrain tiles' flat-colour fallback.
 */
function UnitMarker({
  unit,
  tex,
  hud,
  posOverride,
  alpha = 1,
}: {
  unit: Unit;
  tex: LoadedUnits | null;
  hud: boolean;
  posOverride?: { x: number; y: number; facing?: Facing };
  alpha?: number;
}) {
  const computed = axialToPixel(unit.coord.q, unit.coord.r, HEX_SIZE);
  const { x, y } = posOverride ?? computed;
  const facing = posOverride?.facing ?? unit.facing;
  const color = playerColor(unit.owner);

  const drawFallback = useCallback(
    (g: Graphics) => {
      g.clear();
      const r = HEX_SIZE * 0.62;
      if (unit.type === "infantry") {
        g.circle(0, 0, r);
      } else if (unit.type === "armor") {
        g.roundRect(-r, -r, r * 2, r * 2, 6);
      } else {
        // artillery: upward triangle
        g.poly([0, -r, r, r * 0.8, -r, r * 0.8]);
      }
      g.fill({ color });
      g.stroke({ width: 3, color: 0x000000 });
    },
    [unit.type, color],
  );

  const drawPips = useCallback(
    (g: Graphics) => drawPipBar(g, unit.hp, color),
    [unit.hp, color],
  );

  const pair = tex?.[unit.type] ?? null;
  if (pair === null) {
    return (
      <pixiContainer x={x} y={y} alpha={alpha}>
        <pixiGraphics draw={drawFallback} />
        {hud && (
          <pixiText
            text={String(unit.hp)}
            style={hpStyle}
            anchor={0.5}
            x={0}
            y={2}
          />
        )}
      </pixiContainer>
    );
  }
  // The sprite art natively faces west (units.ts), so `right` mirrors scale.x.
  // Only the two sprite layers flip — the pip bar stays screen-oriented.
  const s = (HEX_SIZE * 2) / pair.base.width;
  const sx = facing === "right" ? -s : s;
  return (
    <pixiContainer x={x} y={y} alpha={alpha}>
      <pixiSprite texture={pair.base} anchor={0.5} scale={{ x: sx, y: s }} />
      <pixiSprite texture={pair.mask} anchor={0.5} scale={{ x: sx, y: s }} tint={color} />
      {hud && showsHealth(unit.hp) && <pixiGraphics draw={drawPips} />}
    </pixiContainer>
  );
}

/**
 * A translucent preview of a staged recruit, sitting on its (empty) city hex.
 * `facing` is the caller's prediction of the engine default (face the nearest
 * enemy) so the ghost previews how the recruit will actually stand.
 */
function RecruitGhost({
  coord,
  type,
  owner,
  facing,
  tex,
}: {
  coord: Axial;
  type: UnitType;
  owner: string;
  facing: Facing;
  tex: LoadedUnits | null;
}) {
  const { x, y } = axialToPixel(coord.q, coord.r, HEX_SIZE);
  const color = playerColor(owner);
  const draw = useCallback(
    (g: Graphics) => {
      g.clear();
      const r = HEX_SIZE * 0.62;
      if (type === "infantry") {
        g.circle(0, 0, r);
      } else if (type === "armor") {
        g.roundRect(-r, -r, r * 2, r * 2, 6);
      } else {
        g.poly([0, -r, r, r * 0.8, -r, r * 0.8]);
      }
      g.fill({ color, alpha: 0.5 });
      g.stroke({ width: 3, color: 0xffffff, alpha: 0.8 });
    },
    [type, color],
  );
  const pair = tex?.[type] ?? null;
  // Same west-native mirror rule as UnitMarker.
  const s = pair === null ? 1 : (HEX_SIZE * 2) / pair.base.width;
  const sx = facing === "right" ? -s : s;
  return (
    <pixiContainer x={x} y={y}>
      {pair === null ? (
        <pixiGraphics draw={draw} />
      ) : (
        <pixiContainer alpha={0.55}>
          <pixiSprite texture={pair.base} anchor={0.5} scale={{ x: sx, y: s }} />
          <pixiSprite texture={pair.mask} anchor={0.5} scale={{ x: sx, y: s }} tint={color} />
        </pixiContainer>
      )}
      <pixiText text="+" style={recruitGhostStyle} anchor={0.5} x={0} y={2} />
    </pixiContainer>
  );
}

/**
 * A movement-pulse badge on one hex of a staged path: a dark disc with the
 * 1-based pulse number the unit arrives on (SPEC §12 — pulse k is the k-th
 * path step). Pulses past the expected ZOC stop grey out.
 */
function PulseBadge({ coord, n, blocked }: { coord: Axial; n: number; blocked: boolean }) {
  const { x, y } = axialToPixel(coord.q, coord.r, HEX_SIZE);
  const drawDisc = useCallback(
    (g: Graphics) => {
      g.clear();
      g.circle(0, 0, PULSE_BADGE_RADIUS);
      g.fill({ color: PULSE_BADGE_FILL, alpha: 0.92 });
      g.stroke({ width: 1.6, color: 0xffffff, alpha: blocked ? 0.35 : 0.7 });
    },
    [blocked],
  );
  return (
    <pixiContainer x={x} y={y}>
      <pixiGraphics draw={drawDisc} />
      <pixiText
        text={String(n)}
        style={blocked ? pulseBadgeBlockedStyle : pulseBadgeStyle}
        anchor={0.5}
      />
    </pixiContainer>
  );
}

/**
 * A small text badge on a unit with a staged rest/hold order, in the
 * below-sprite slot (`unitHud.ts` — the health slot took the space above).
 */
function OrderBadge({ unit, label }: { unit: Unit; label: "rest" | "hold" }) {
  const { x, y } = axialToPixel(unit.coord.q, unit.coord.r, HEX_SIZE);
  return (
    <pixiText
      text={label}
      style={orderBadgeStyle}
      anchor={0.5}
      x={x}
      y={y + ORDER_BADGE_Y}
    />
  );
}

/** Small overlay describing the clicked hex — proves hit-testing end to end. */
function HexInfo({ state, selected }: { state: GameState; selected: Axial }) {
  const hex = (state.map.hexes ?? []).find(
    (h) => h.coord.q === selected.q && h.coord.r === selected.r,
  );
  const unit = (state.units ?? []).find(
    (u) => u.coord.q === selected.q && u.coord.r === selected.r,
  );
  const settlement = (state.settlements ?? []).find(
    (s) => s.coord.q === selected.q && s.coord.r === selected.r,
  );

  return (
    <div className="absolute bottom-2 left-2 rounded-md border border-zinc-700 bg-zinc-900/90 px-3 py-2 text-xs text-zinc-200">
      <div className="font-mono text-zinc-400">
        ({selected.q}, {selected.r})
      </div>
      {hex !== undefined && (
        <div className="capitalize">
          {hex.terrain}
          {hex.road ? " · road" : ""}
        </div>
      )}
      {settlement !== undefined && (
        <div className="text-zinc-400">
          {settlement.controlled_by === null
            ? "neutral"
            : `held by ${settlement.controlled_by}`}
        </div>
      )}
      {unit !== undefined && (
        <div className="capitalize text-zinc-300">
          {unit.owner} {unit.type} · {unit.hp} hp
        </div>
      )}
    </div>
  );
}
