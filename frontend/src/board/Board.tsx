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
import { reachableHexes } from "@/planning/reach";
import type { PlanningController } from "@/planning/usePlanning";

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
  HEX_STROKE,
  NEUTRAL_COLOR,
  playerColor,
  TERRAIN_FILL,
} from "./palette";

extend({ Container, Graphics, Text });

type GameState = components["schemas"]["GameState"];
type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];

const HEX_SIZE = 16;
const BACKGROUND = 0x0a0a0b;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;
const DRAG_THRESHOLD_PX = 4;

// Planning overlay colours (range halo + staged path).
const RANGE_FILL = 0xffe066;
const RANGE_ALPHA = 0.16;
const PATH_COLOR = 0xffe066;
const PATH_DIM_COLOR = 0x8a8a8a;

const hpStyle = new TextStyle({
  fontFamily: "monospace",
  fontSize: 11,
  fontWeight: "bold",
  fill: 0xffffff,
  stroke: { color: 0x000000, width: 3 },
});

interface View {
  x: number;
  y: number;
  zoom: number;
}

export interface BoardProps {
  state: GameState;
  /** The viewer's slot; reserved for own-unit emphasis in later slices. */
  yourSlot: string;
  /** Planning controller, supplied only when it is the viewer's move to plan. */
  planning?: PlanningController;
}

export function Board({ state, planning }: BoardProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [selected, setSelected] = useState<Axial | null>(null);

  const hexes = useMemo(() => state.map.hexes ?? [], [state.map.hexes]);
  const units = useMemo(() => state.units ?? [], [state.units]);
  const settlements = useMemo(() => state.settlements ?? [], [state.settlements]);

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
  const selectedUnitId = planning?.selectedUnitId ?? null;
  const selectedUnit = useMemo(() => {
    if (selectedUnitId === null) return null;
    return units.find((u) => u.id === selectedUnitId) ?? null;
  }, [selectedUnitId, units]);

  const reachable = useMemo(
    () => (selectedUnit === null ? null : reachableHexes(selectedUnit, hexByKey)),
    [selectedUnit, hexByKey],
  );

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

  // Fit the whole map into view the first time we know the viewport size.
  // Computed during render (a guarded, converging setState — React's blessed
  // "initialize state from props" pattern) rather than in an effect.
  if (size !== null && size.width > 0 && size.height > 0 && view === null && hexes.length > 0) {
    const bounds = mapPixelBounds(
      hexes.map((h) => h.coord),
      HEX_SIZE,
    );
    const zoom = Math.max(
      MIN_ZOOM,
      Math.min(MAX_ZOOM, Math.min(size.width / bounds.width, size.height / bounds.height)),
    );
    setView({
      zoom,
      x: size.width / 2 - (bounds.minX + bounds.width / 2) * zoom,
      y: size.height / 2 - (bounds.minY + bounds.height / 2) * zoom,
    });
  }

  // --- input: drag-pan, wheel-zoom, click-to-select (all DOM-level) ---
  const dragRef = useRef<{ startX: number; startY: number; vx: number; vy: number; moved: boolean } | null>(null);

  const screenToWorld = useCallback(
    (clientX: number, clientY: number, v: View): { x: number; y: number } => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      const sx = clientX - (rect?.left ?? 0);
      const sy = clientY - (rect?.top ?? 0);
      return { x: (sx - v.x) / v.zoom, y: (sy - v.y) / v.zoom };
    },
    [],
  );

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if (view === null) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      vx: view.x,
      vy: view.y,
      moved: false,
    };
  }, [view]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (drag === null) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (Math.abs(dx) > DRAG_THRESHOLD_PX || Math.abs(dy) > DRAG_THRESHOLD_PX) {
      drag.moved = true;
    }
    setView((v) => (v === null ? v : { ...v, x: drag.vx + dx, y: drag.vy + dy }));
  }, []);

  const onPointerUp = useCallback((e: React.PointerEvent) => {
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
    } else {
      // Inspect mode: clicks surface hex info.
      setSelected(onMap ? ax : null);
    }
  }, [view, screenToWorld, hexByKey, planning]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    setView((v) => {
      if (v === null) return v;
      const rect = wrapperRef.current?.getBoundingClientRect();
      const sx = e.clientX - (rect?.left ?? 0);
      const sy = e.clientY - (rect?.top ?? 0);
      const factor = Math.exp(-e.deltaY * 0.0015);
      const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, v.zoom * factor));
      // keep the point under the cursor fixed while zooming
      const worldX = (sx - v.x) / v.zoom;
      const worldY = (sy - v.y) / v.zoom;
      return { zoom, x: sx - worldX * zoom, y: sy - worldY * zoom };
    });
  }, []);

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

  // Planning layer: range halo for the selected unit, the staged paths (the
  // selected unit's emphasized), and a ring on the selected unit.
  const drawPlanning = useCallback(
    (g: Graphics) => {
      g.clear();
      if (planning === undefined) return;

      // movement range halo for the selected unit
      if (reachable !== null) {
        for (const key of reachable) {
          const hex = hexByKey.get(key);
          if (hex === undefined) continue;
          const { x, y } = axialToPixel(hex.coord.q, hex.coord.r, HEX_SIZE);
          g.poly(hexCornerPoints(x, y, HEX_SIZE));
          g.fill({ color: RANGE_FILL, alpha: RANGE_ALPHA });
        }
      }

      // staged paths: a polyline from each unit through its planned hexes
      const unitById = new Map(units.map((u) => [u.id, u] as const));
      for (const [idStr, path] of Object.entries(planning.paths)) {
        if (path.length === 0) continue;
        const unit = unitById.get(Number(idStr));
        if (unit === undefined) continue;
        const active = Number(idStr) === planning.selectedUnitId;
        const color = active ? PATH_COLOR : PATH_DIM_COLOR;

        const start = axialToPixel(unit.coord.q, unit.coord.r, HEX_SIZE);
        g.moveTo(start.x, start.y);
        for (const step of path) {
          const p = axialToPixel(step.q, step.r, HEX_SIZE);
          g.lineTo(p.x, p.y);
        }
        g.stroke({ width: active ? 3 : 2, color, alpha: active ? 1 : 0.7 });

        // a node at each waypoint; the destination larger
        path.forEach((step, i) => {
          const p = axialToPixel(step.q, step.r, HEX_SIZE);
          g.circle(p.x, p.y, i === path.length - 1 ? 4 : 2.5);
          g.fill({ color });
        });
      }

      // ring on the selected unit
      if (selectedUnit !== null) {
        const { x, y } = axialToPixel(selectedUnit.coord.q, selectedUnit.coord.r, HEX_SIZE);
        g.poly(hexCornerPoints(x, y, HEX_SIZE));
        g.stroke({ width: 2.5, color: 0xffffff });
      }
    },
    [planning, reachable, hexByKey, units, selectedUnit],
  );

  return (
    <div
      ref={wrapperRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
      onWheel={onWheel}
      className="relative h-full w-full cursor-grab touch-none overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950 active:cursor-grabbing"
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
          </pixiContainer>
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
