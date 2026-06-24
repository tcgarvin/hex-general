// Phase 8 board + Phase 9 planning input. Renders a fog-filtered GameState as
// flat-colour hex tiles + tinted unit markers, with pan/zoom and click→(q,r)
// hit-testing. When a `planning` controller is supplied (your move), clicks drive
// unit selection / exact-path building and the board overlays the movement range
// and staged paths. No sprites yet — terrain is solid fills (see palette.ts);
// Phase 8 Slice 2 swaps Graphics for the AI-generated sprite set.

import { Application, extend } from "@pixi/react";
import { Container, Graphics, Text, TextStyle } from "pixi.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { components } from "@/lib/api/schema";
import { reachableContinuations } from "@/planning/reach";
import type { Planning } from "@/planning/usePlanning";

import {
  AXIAL_DIRECTIONS,
  axialToPixel,
  hexCornerPoints,
  hexKey,
  mapPixelBounds,
  pixelToAxial,
  type Axial,
} from "./hex";
import {
  fullMapLayout,
  pointInRect,
  surfaceLayout,
  type SurfaceLayout,
} from "./layout";
import {
  HEX_STROKE,
  NEUTRAL_COLOR,
  playerColor,
  TERRAIN_FILL,
} from "./palette";
import { LockPanel } from "./panels/LockPanel";
import { RosterPanel } from "./panels/RosterPanel";
import { orderLabelOf, rosterRowAt, rosterRows } from "./panels/roster";
import { SelectedUnitPanel } from "./panels/SelectedUnitPanel";
import { paletteButtonAt, type PaletteAction } from "./panels/selectedUnit";

extend({ Container, Graphics, Text });

type GameState = components["schemas"]["GameState"];
type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];

const HEX_SIZE = 16;
const BACKGROUND = 0x0a0a0b;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;
const DRAG_THRESHOLD_PX = 4;

// Planning overlay colours (range outline + staged/preview paths).
const RANGE_OUTLINE = 0xffe066;
const PATH_COLOR = 0xffe066;
const PATH_STAGED_COLOR = 0xc9a227;
const PREVIEW_COLOR = 0xfff4c2;
// Barrage overlay (target range + staged shot), in a distinct red/orange.
const BARRAGE_RANGE = 0xff7043;
const BARRAGE_COLOR = 0xff5252;

// Stable empty path so unkeyed lookups don't churn memo identities.
const EMPTY_PATH: readonly Axial[] = [];

const hpStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 11,
  fontWeight: "bold",
  fill: 0xffffff,
  stroke: { color: 0x000000, width: 3 },
});

// Badge over a unit with a staged rest/hold order (move/barrage draw geometry).
const orderBadgeStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 9,
  fontWeight: "bold",
  fill: 0xffe066,
  stroke: { color: 0x000000, width: 3 },
});

interface View {
  x: number;
  y: number;
  zoom: number;
}

/** Lock-control state + action (UI-SPEC §11), supplied only while planning. */
export interface LockState {
  ordered: number;
  total: number;
  idle: number;
  submitting: boolean;
  error: string | null;
  onLock: () => void;
}

export interface BoardProps {
  state: GameState;
  /** The viewer's slot; reserved for own-unit emphasis in later slices. */
  yourSlot: string;
  /** Planning state, supplied only when it is the viewer's move to plan. */
  planning?: Planning;
  /** Lock control, supplied alongside `planning` (UI-SPEC §11). */
  lock?: LockState;
}

export function Board({ state, yourSlot, planning, lock }: BoardProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [selected, setSelected] = useState<Axial | null>(null);

  const hexes = useMemo(() => state.map.hexes ?? [], [state.map.hexes]);
  const units = useMemo(() => state.units ?? [], [state.units]);
  const settlements = useMemo(() => state.settlements ?? [], [state.settlements]);

  // --- docked-panel layout (UI-SPEC §2): the roster reserves the left edge while
  // it's the viewer's move; otherwise the map fills the whole surface. ---
  // Panels (roster + bottom dock) appear only while it is the viewer's move.
  const showPanels = planning !== undefined;
  const layout = useMemo<SurfaceLayout | null>(() => {
    if (size === null) return null;
    return showPanels
      ? surfaceLayout(size.width, size.height)
      : fullMapLayout(size.width, size.height);
  }, [size, showPanels]);

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
  const rows = useMemo(
    () => (planning === undefined ? [] : rosterRows(ownUnits, planning, enemyHexKeys)),
    [planning, ownUnits, enemyHexKeys],
  );
  const [rosterHover, setRosterHover] = useState<number | null>(null);

  const hexByKey = useMemo(() => {
    const m = new Map<string, Hex>();
    for (const h of hexes) m.set(hexKey(h.coord.q, h.coord.r), h);
    return m;
  }, [hexes]);

  const settlementOwner = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const s of settlements) m.set(hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null);
    return m;
  }, [settlements]);

  // --- planning overlays (only when a controller is supplied) ---
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  const selectedUnitId = planning?.selectedUnitId ?? null;
  const mode = planning?.mode ?? "move";
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
  // Only in move mode — while aiming a barrage, the move range is hidden.
  const continuations = useMemo(
    () =>
      selectedUnit === null || mode !== "move"
        ? null
        : reachableContinuations(selectedUnit, selectedPath, hexByKey),
    [selectedUnit, mode, selectedPath, hexByKey],
  );

  // The route a click at the hovered hex would commit (origin → hover). Resolved
  // against the current selection's routes, so a stale hover simply yields no
  // preview rather than a wrong one.
  const previewPath =
    hoverKey !== null ? (continuations?.get(hoverKey) ?? null) : null;

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

  // Fit the whole map into the *map rectangle* (the surface minus docked panels)
  // the first time we know the viewport size. Computed during render (a guarded,
  // converging setState — React's blessed "initialize from props" pattern).
  if (layout !== null && layout.map.width > 0 && layout.map.height > 0 && view === null && hexes.length > 0) {
    const { map } = layout;
    const bounds = mapPixelBounds(
      hexes.map((h) => h.coord),
      HEX_SIZE,
    );
    const zoom = Math.max(
      MIN_ZOOM,
      Math.min(MAX_ZOOM, Math.min(map.width / bounds.width, map.height / bounds.height)),
    );
    setView({
      zoom,
      x: map.x + map.width / 2 - (bounds.minX + bounds.width / 2) * zoom,
      y: map.y + map.height / 2 - (bounds.minY + bounds.height / 2) * zoom,
    });
  }

  // Recenter the map rectangle on a world coordinate (roster row → jump to unit).
  const centerOn = useCallback(
    (coord: Axial) => {
      setView((v) => {
        if (v === null || layout === null) return v;
        const p = axialToPixel(coord.q, coord.r, HEX_SIZE);
        return {
          ...v,
          x: layout.map.x + layout.map.width / 2 - p.x * v.zoom,
          y: layout.map.y + layout.map.height / 2 - p.y * v.zoom,
        };
      });
    },
    [layout],
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

  /** Whether a canvas point is over the bottom dock (selected-unit or lock). */
  const overBottom = useCallback(
    (sx: number, sy: number): boolean =>
      showPanels &&
      layout !== null &&
      (pointInRect(sx, sy, layout.selectedUnit) || pointInRect(sx, sy, layout.lock)),
    [showPanels, layout],
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
        bottomPressRef.current = true;
        rosterPressRef.current = false;
        return;
      }
      rosterPressRef.current = false;
      bottomPressRef.current = false;
      e.currentTarget.setPointerCapture(e.pointerId);
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        vx: view.x,
        vy: view.y,
        moved: false,
      };
    },
    [view, localPoint, overRoster, overBottom],
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
        setView((v) => (v === null ? v : { ...v, x: drag.vx + dx, y: drag.vy + dy }));
        return;
      }
      // Hovering the roster: highlight the row under the pointer (no map preview).
      const { sx, sy } = localPoint(e.clientX, e.clientY);
      if (overRoster(sx, sy) && layout !== null) {
        const idx = rosterRowAt(sy - layout.roster.y, rows.length);
        setRosterHover((prev) => (prev === idx ? prev : idx));
        setHoverKey(null);
        return;
      }
      setRosterHover((prev) => (prev === null ? prev : null));
      // Over the bottom dock: no map preview (and no roster row highlight).
      if (overBottom(sx, sy)) {
        setHoverKey((prev) => (prev === null ? prev : null));
        return;
      }
      // Over the map: in move mode with a unit selected, track the hovered hex so
      // the board can preview the route a click would commit (no preview while
      // aiming a barrage).
      if (planning === undefined || selectedUnitId === null || mode !== "move" || view === null)
        return;
      const world = screenToWorld(e.clientX, e.clientY, view);
      const ax = pixelToAxial(world.x, world.y, HEX_SIZE);
      const key = hexKey(ax.q, ax.r);
      const next = hexByKey.has(key) ? key : null;
      setHoverKey((prev) => (prev === next ? prev : next));
    },
    [planning, selectedUnitId, mode, view, screenToWorld, hexByKey, localPoint, overRoster, overBottom, layout, rows.length],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      // A press that started on the roster: select the clicked row's unit + jump.
      if (rosterPressRef.current) {
        rosterPressRef.current = false;
        const { sx, sy } = localPoint(e.clientX, e.clientY);
        if (planning !== undefined && layout !== null && overRoster(sx, sy)) {
          const idx = rosterRowAt(sy - layout.roster.y, rows.length);
          if (idx !== null) {
            const row = rows[idx];
            planning.selectUnit(row.unit.id);
            centerOn(row.unit.coord);
          }
        }
        return;
      }

      // A press that started on the bottom dock: route to a palette / lock button.
      if (bottomPressRef.current) {
        bottomPressRef.current = false;
        const { sx, sy } = localPoint(e.clientX, e.clientY);
        if (planning === undefined || layout === null) return;
        if (pointInRect(sx, sy, layout.selectedUnit)) {
          const unit = planning.selectedUnit;
          if (unit !== null) {
            const action = paletteButtonAt(
              sx - layout.selectedUnit.x,
              sy - layout.selectedUnit.y,
              unit,
            );
            if (action !== null) runPaletteAction(action, unit.id);
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
        // Planning mode: clicks select units and build paths (off-map is ignored).
        if (onMap) planning.onHexClick(ax);
        // The committed path may have moved; recompute the preview on the next move.
        setHoverKey(null);
      } else {
        // Inspect mode: clicks surface hex info.
        setSelected(onMap ? ax : null);
      }
    },
    [view, screenToWorld, hexByKey, planning, lock, localPoint, overRoster, layout, rows, centerOn, runPaletteAction],
  );

  const onPointerLeave = useCallback(() => {
    dragRef.current = null;
    rosterPressRef.current = false;
    bottomPressRef.current = false;
    setHoverKey(null);
    setRosterHover(null);
  }, []);

  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      const { sx, sy } = localPoint(e.clientX, e.clientY);
      if (overRoster(sx, sy) || overBottom(sx, sy)) return; // panels don't zoom the map
      setView((v) => {
        if (v === null) return v;
        const factor = Math.exp(-e.deltaY * 0.0015);
        const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, v.zoom * factor));
        // keep the point under the cursor fixed while zooming
        const worldX = (sx - v.x) / v.zoom;
        const worldY = (sy - v.y) / v.zoom;
        return { zoom, x: sx - worldX * zoom, y: sy - worldY * zoom };
      });
    },
    [localPoint, overRoster, overBottom],
  );

  // --- draw callbacks ---
  const drawTerrain = useCallback(
    (g: Graphics) => {
      g.clear();

      // tiles
      for (const h of hexes) {
        const { x, y } = axialToPixel(h.coord.q, h.coord.r, HEX_SIZE);
        const pts = hexCornerPoints(x, y, HEX_SIZE);
        g.poly(pts);
        g.fill({ color: TERRAIN_FILL[h.terrain] });
        g.stroke({ width: 1, color: HEX_STROKE });
      }

      // road network: link road/bridge hexes centre-to-edge toward road neighbours
      for (const h of hexes) {
        if (!h.road && h.terrain !== "bridge") continue;
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
      g.stroke({ width: 3, color: 0x6b4f2a });

      // settlement ownership rings
      for (const [key, owner] of settlementOwner) {
        const hex = hexByKey.get(key);
        if (hex === undefined) continue;
        const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE - 1.5));
        g.stroke({
          width: 3,
          color: owner === null ? NEUTRAL_COLOR : playerColor(owner),
        });
      }
    },
    [hexes, hexByKey, settlementOwner],
  );

  const drawSelection = useCallback(
    (g: Graphics) => {
      g.clear();
      if (selected === null) return;
      const { x, y } = axialToPixel(selected.q, selected.r, HEX_SIZE);
      g.poly(hexCornerPoints(x, y, HEX_SIZE));
      g.stroke({ width: 2.5, color: 0xffffff });
    },
    [selected],
  );

  // Planning layer: the selected unit's movement-range outline, every staged
  // path (selected emphasized, others still clearly drawn so pending orders stay
  // visible), the hovered route preview, and a ring on the selected unit.
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
          g.poly(hexCornerPoints(x, y, HEX_SIZE - 1.5));
        }
        g.stroke({ width: 1.5, color: RANGE_OUTLINE, alpha: 0.6 });
      }

      // barrage range: outline every hex the selected artillery may target
      if (planning.barrageTargetKeys.size > 0) {
        for (const key of planning.barrageTargetKeys) {
          const hex = hexByKey.get(key);
          if (hex === undefined) continue;
          const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
          g.poly(hexCornerPoints(x, y, HEX_SIZE - 1.5));
        }
        g.stroke({ width: 1.5, color: BARRAGE_RANGE, alpha: 0.6 });
      }

      // staged orders: draw each unit's plan so pending orders read at a glance
      const unitById = new Map(units.map((u) => [u.id, u] as const));
      for (const [idStr, order] of Object.entries(planning.stagedByUnit)) {
        const unit = unitById.get(Number(idStr));
        if (unit === undefined) continue;
        const active = Number(idStr) === planning.selectedUnitId;
        const origin = axialToPixel(unit.coord.q, unit.coord.r, HEX_SIZE);

        if (order.kind === "move") {
          if (order.path.length === 0) continue;
          const color = active ? PATH_COLOR : PATH_STAGED_COLOR;
          g.moveTo(origin.x, origin.y);
          for (const step of order.path) {
            const p = axialToPixel(step.q, step.r, HEX_SIZE);
            g.lineTo(p.x, p.y);
          }
          g.stroke({ width: active ? 3 : 2.5, color, alpha: active ? 1 : 0.9 });

          // a node at each waypoint; the destination larger
          order.path.forEach((step, i) => {
            const p = axialToPixel(step.q, step.r, HEX_SIZE);
            g.circle(p.x, p.y, i === order.path.length - 1 ? 4 : 2.5);
            g.fill({ color });
          });
        } else if (order.kind === "barrage") {
          // a line from the gun to its target + a reticle on the target hex
          const t = axialToPixel(order.target.q, order.target.r, HEX_SIZE);
          g.moveTo(origin.x, origin.y);
          g.lineTo(t.x, t.y);
          g.stroke({ width: 2, color: BARRAGE_COLOR, alpha: active ? 1 : 0.85 });
          g.circle(t.x, t.y, 6);
          g.stroke({ width: 2, color: BARRAGE_COLOR });
          g.moveTo(t.x - 9, t.y);
          g.lineTo(t.x + 9, t.y);
          g.moveTo(t.x, t.y - 9);
          g.lineTo(t.x, t.y + 9);
          g.stroke({ width: 1.5, color: BARRAGE_COLOR });
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
          const start = axialToPixel(from.q, from.r, HEX_SIZE);
          g.moveTo(start.x, start.y);
          for (const step of segment) {
            const p = axialToPixel(step.q, step.r, HEX_SIZE);
            g.lineTo(p.x, p.y);
          }
          g.stroke({ width: 2, color: PREVIEW_COLOR, alpha: 0.85 });

          const dest = segment[segment.length - 1];
          const dp = axialToPixel(dest.q, dest.r, HEX_SIZE);
          g.circle(dp.x, dp.y, 4);
          g.fill({ color: PREVIEW_COLOR, alpha: 0.85 });
        }
      }

      // ring on the selected unit
      if (selectedUnit !== null) {
        const { x, y } = axialToPixel(selectedUnit.coord.q, selectedUnit.coord.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE));
        g.stroke({ width: 2.5, color: 0xffffff });
      }
    },
    [planning, continuations, hexByKey, units, selectedUnit, selectedPath, previewPath],
  );

  // The selected unit's staged order + its §5.2 display label, for the detail panel.
  const selectedStaged =
    selectedUnitId === null ? undefined : planning?.stagedByUnit[selectedUnitId];
  const selectedOrderLabel = orderLabelOf(selectedStaged, enemyHexKeys);

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
            <pixiGraphics draw={drawTerrain} />
            <pixiGraphics draw={drawSelection} />
            <pixiGraphics draw={drawPlanning} />
            {units.map((u) => (
              <UnitMarker key={u.id} unit={u} />
            ))}
            {planning !== undefined &&
              units.map((u) => {
                const order = planning.stagedByUnit[u.id];
                if (order === undefined || (order.kind !== "rest" && order.kind !== "hold"))
                  return null;
                return <OrderBadge key={`badge-${u.id}`} unit={u} label={order.kind} />;
              })}
          </pixiContainer>
          {showPanels && layout !== null && (
            <>
              <RosterPanel rect={layout.roster} rows={rows} hoverIndex={rosterHover} />
              <SelectedUnitPanel
                rect={layout.selectedUnit}
                unit={planning?.selectedUnit ?? null}
                staged={selectedStaged}
                mode={mode}
                orderLabel={selectedOrderLabel}
              />
              {lock !== undefined && (
                <LockPanel
                  rect={layout.lock}
                  ordered={lock.ordered}
                  total={lock.total}
                  idle={lock.idle}
                  submitting={lock.submitting}
                  error={lock.error}
                />
              )}
            </>
          )}
        </Application>
      )}
      {planning === undefined && selected !== null && (
        <HexInfo state={state} selected={selected} />
      )}
    </div>
  );
}

/** A single unit: owner-tinted marker (shape encodes type) + hp text. */
function UnitMarker({ unit }: { unit: Unit }) {
  const { x, y } = axialToPixel(unit.coord.q, unit.coord.r, HEX_SIZE);
  const color = playerColor(unit.owner);

  const draw = useCallback(
    (g: Graphics) => {
      g.clear();
      const r = HEX_SIZE * 0.62;
      if (unit.type === "infantry") {
        g.circle(0, 0, r);
      } else if (unit.type === "armor") {
        g.roundRect(-r, -r, r * 2, r * 2, 3);
      } else {
        // artillery: upward triangle
        g.poly([0, -r, r, r * 0.8, -r, r * 0.8]);
      }
      g.fill({ color });
      g.stroke({ width: 1.5, color: 0x000000 });
    },
    [unit.type, color],
  );

  return (
    <pixiContainer x={x} y={y}>
      <pixiGraphics draw={draw} />
      <pixiText
        text={String(unit.hp)}
        style={hpStyle}
        anchor={0.5}
        x={0}
        y={1}
      />
    </pixiContainer>
  );
}

/** A small text badge above a unit with a staged rest/hold order. */
function OrderBadge({ unit, label }: { unit: Unit; label: "rest" | "hold" }) {
  const { x, y } = axialToPixel(unit.coord.q, unit.coord.r, HEX_SIZE);
  return (
    <pixiText
      text={label}
      style={orderBadgeStyle}
      anchor={0.5}
      x={x}
      y={y - HEX_SIZE * 0.95}
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
