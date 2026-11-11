// Planning a world turn (W6 slice 2): selection, board clicks, the staged plan and
// submission.
//
// The plan is keyed to the turn. Until the player edits, it *is* whatever the
// server holds for this turn (`view.submission`), so a reload or a second device
// shows the plan already in; editing forks a local copy, and a new turn drops it.
// A world player may resubmit until the clock resolves the turn, and each submit
// replaces the whole plan (WORLD_SPEC §10.1), so the local copy is always sent in
// full.

import { useCallback, useMemo, useState } from "react";

import { axialEqual, axialNeighbors, hexKey, type Axial, type Hex } from "@/board/hex";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { reachableContinuations, type HexIndex } from "@/planning/reach";
import { isPassable, UNIT_MOVE } from "@/planning/rules";
import type { components } from "@/lib/api/schema";

import type { Building } from "./economy";
import {
  inBarrageRange,
  planFromSubmission,
  samePlan,
  toSubmission,
  type WorldOrder,
  type WorldPlan,
} from "./plan";
import { columnCosts, findRoute, travelEta, type ColumnCosts } from "./routing";
import type { WorldView } from "./useWorld";
import type { WorldMap } from "./worldMap";

type Unit = components["schemas"]["Unit"];
type UnitType = components["schemas"]["UnitType"];
type Posture = components["schemas"]["Posture"];

/** What a board click means while a unit is selected. */
export type PlanMode = "travel" | "move" | "barrage";

/** A unit's planned path this turn or beyond, for drawing and the panels. */
export interface PlannedRoute {
  unitId: number;
  from: Axial;
  path: Axial[];
  /** `standing`: a travel order from an earlier turn that will carry on. */
  kind: "travel" | "standing" | "move" | "march";
  /** Turns to arrive (travel and standing only). */
  eta: number | null;
}

export interface WorldPlanning {
  plan: WorldPlan;
  /** The local plan differs from what the server holds. */
  dirty: boolean;
  /** The server holds a plan for this turn. */
  submitted: boolean;
  /** The previous turn resolved while it had edits that were never sent. */
  droppedTurn: number | null;
  selectedUnit: Unit | null;
  selectedHex: Axial | null;
  mode: PlanMode;
  routes: readonly PlannedRoute[];
  /** Hexes a click would extend the selected unit's move to (move mode). */
  moveReach: ReadonlyMap<string, Axial[]>;
  onHexClick: (hex: Axial) => void;
  selectUnit: (unitId: number | null) => void;
  setMode: (mode: PlanMode) => void;
  setOrder: (unitId: number, order: WorldOrder | null) => void;
  setOnArrival: (unitId: number, posture: Posture) => void;
  setBuild: (building: Building | null) => void;
  setRecruit: (coord: Axial, type: UnitType | null) => void;
  toggleDisband: (unitId: number) => void;
  /** Throw away local edits and go back to what the server holds. */
  revert: () => void;
  submit: () => Promise<void>;
  submitting: boolean;
  submitError: string | null;
}

export function useWorldPlan(view: WorldView, map: WorldMap, onSubmitted: () => void): WorldPlanning {
  const slot = view.your_slot;
  const state = view.state;
  const server = useMemo(() => planFromSubmission(view.submission), [view.submission]);
  // `sent`: the server accepted this exact local plan (so a turn resolving under
  // it loses nothing).
  const [local, setLocal] = useState<{ turn: number; plan: WorldPlan; sent: boolean } | null>(null);
  const plan = local !== null && local.turn === view.turn ? local.plan : server;
  const droppedTurn = local !== null && local.turn < view.turn && !local.sent ? local.turn : null;
  const dirty = !samePlan(plan, server, slot);

  const [selection, setSelection] = useState<{ turn: number; unitId: number | null; hex: Axial | null; mode: PlanMode }>(
    { turn: view.turn, unitId: null, hex: null, mode: "travel" },
  );
  const sel = useMemo(
    () => (selection.turn === view.turn ? selection : { turn: view.turn, unitId: null, hex: null, mode: "travel" as const }),
    [selection, view.turn],
  );
  const ownUnits = useMemo(() => state.units.filter((u) => u.owner === slot), [state.units, slot]);
  const selectedUnit = ownUnits.find((u) => u.id === sel.unitId) ?? null;

  const costs = useMemo(() => columnCosts(map), [map]);

  const edit = useCallback(
    (change: (p: WorldPlan) => WorldPlan) => {
      setLocal((prev) => {
        const base = prev !== null && prev.turn === view.turn ? prev.plan : server;
        return { turn: view.turn, plan: change(base), sent: false };
      });
    },
    [server, view.turn],
  );

  const setOrder = useCallback(
    (unitId: number, order: WorldOrder | null) =>
      edit((p) => {
        const orders = { ...p.orders };
        if (order === null) delete orders[unitId];
        else orders[unitId] = order;
        return { ...p, orders };
      }),
    [edit],
  );

  const routes = useMemo(
    () => plannedRoutes(ownUnits, plan, state.standing_orders ?? [], costs),
    [ownUnits, plan, state.standing_orders, costs],
  );

  const moveReach = useMemo(() => {
    if (selectedUnit === null || sel.mode !== "move" || selectedUnit.posture === "column") return new Map<string, Axial[]>();
    const order = plan.orders[selectedUnit.id];
    const base = order?.kind === "move" ? order.path : [];
    const index = localIndex(map, selectedUnit.coord, UNIT_MOVE[selectedUnit.type] + 2);
    const reach = reachableContinuations(selectedUnit, [], index);
    if (base.length > 0) for (const [k, v] of reachableContinuations(selectedUnit, base, index)) reach.set(k, v);
    return reach;
  }, [selectedUnit, sel.mode, plan.orders, map]);

  const select = useCallback(
    (unitId: number | null, hex: Axial | null, mode: PlanMode = "travel") =>
      setSelection({ turn: view.turn, unitId, hex, mode }),
    [view.turn],
  );

  const onHexClick = useCallback(
    (hex: Axial) => {
      const key = hexKey(hex.q, hex.r);
      if (selectedUnit !== null && sel.mode === "barrage") {
        if (inBarrageRange(selectedUnit, hex) && !axialEqual(hex, selectedUnit.coord)) {
          setOrder(selectedUnit.id, { kind: "barrage", target: hex });
        }
        select(selectedUnit.id, sel.hex, "travel");
        return;
      }
      if (selectedUnit !== null && sel.mode === "move") {
        const path = moveReach.get(key);
        if (path !== undefined) {
          setOrder(selectedUnit.id, { kind: "move", path });
          return;
        }
      }
      const here = ownUnits.filter((u) => u.coord.q === hex.q && u.coord.r === hex.r);
      const at = here.findIndex((u) => u.id === sel.unitId);
      if (here.length > 0 && at < here.length - 1) {
        // Clicking a stack again steps through its units, then the hex itself
        // (its site panel: recruiting, garrison).
        select(here[at + 1].id, hex);
        return;
      }
      if (at >= 0) {
        select(null, hex);
        return;
      }
      if (selectedUnit !== null && sel.mode === "travel") {
        const cell = map.hexAt(hex.q, hex.r);
        if (cell !== undefined && isPassable(cell.terrain) && cachedRoute(costs, selectedUnit.coord, hex, []) !== null) {
          const prev = plan.orders[selectedUnit.id];
          const onArrival = prev?.kind === "travel" ? prev.onArrival : "deployed";
          setOrder(selectedUnit.id, { kind: "travel", destination: hex, via: [], onArrival });
          return;
        }
      }
      select(null, hex);
    },
    [selectedUnit, sel, moveReach, ownUnits, map, costs, plan.orders, setOrder, select],
  );

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submit = useCallback(async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const { error } = await api.POST("/worlds/{game_id}/orders", {
        params: { path: { game_id: view.game_id } },
        body: toSubmission(plan, slot),
      });
      if (error) setSubmitError(describeApiError(error));
      else {
        const turn = view.turn;
        setLocal((prev) => (prev !== null && prev.turn === turn && prev.plan === plan ? { ...prev, sent: true } : prev));
        onSubmitted();
      }
    } catch (err) {
      setSubmitError(describeApiError(err));
    } finally {
      setSubmitting(false);
    }
  }, [plan, slot, view.game_id, view.turn, onSubmitted]);

  return {
    plan,
    dirty,
    submitted: view.submission !== null && view.submission !== undefined,
    droppedTurn,
    selectedUnit,
    selectedHex: sel.hex,
    mode: selectedUnit === null ? "travel" : sel.mode,
    routes,
    moveReach,
    onHexClick,
    selectUnit: (unitId) => {
      const unit = ownUnits.find((u) => u.id === unitId);
      select(unitId, unit?.coord ?? null);
    },
    setMode: (mode) => select(sel.unitId, sel.hex, mode),
    setOrder,
    setOnArrival: (unitId, posture) =>
      edit((p) => {
        const order = p.orders[unitId];
        if (order?.kind !== "travel") return p;
        return { ...p, orders: { ...p.orders, [unitId]: { ...order, onArrival: posture } } };
      }),
    setBuild: (building) => edit((p) => ({ ...p, build: building })),
    setRecruit: (coord, type) =>
      edit((p) => {
        const recruits = { ...p.recruits };
        const key = hexKey(coord.q, coord.r);
        if (type === null) delete recruits[key];
        else recruits[key] = { coord, type };
        return { ...p, recruits };
      }),
    toggleDisband: (unitId) =>
      edit((p) => ({
        ...p,
        disband: p.disband.includes(unitId) ? p.disband.filter((i) => i !== unitId) : [...p.disband, unitId],
      })),
    revert: () => setLocal(null),
    submit,
    submitting,
    submitError,
  };
}

/** Every own unit's path: fresh orders this turn, else the standing travel order. */
function plannedRoutes(
  units: readonly Unit[],
  plan: WorldPlan,
  standing: readonly components["schemas"]["StandingTravel"][],
  costs: ColumnCosts,
): PlannedRoute[] {
  const routes: PlannedRoute[] = [];
  const standingBy = new Map(standing.map((s) => [s.unit_id, s]));
  for (const unit of units) {
    const order = plan.orders[unit.id];
    const packed = unit.posture === "column";
    if (order === undefined) {
      const s = standingBy.get(unit.id);
      if (s !== undefined) {
        routes.push({ unitId: unit.id, from: unit.coord, path: s.route, kind: "standing", eta: travelEta(costs, s.route, { packed }) });
      }
    } else if (order.kind === "travel") {
      const path = cachedRoute(costs, unit.coord, order.destination, order.via);
      // The server re-checks reachability; a staged order always had a route when set.
      if (path === null) throw new Error(`staged travel for unit ${unit.id} has no route`);
      routes.push({ unitId: unit.id, from: unit.coord, path, kind: "travel", eta: travelEta(costs, path, { packed }) });
    } else if (order.kind === "move" || order.kind === "march") {
      routes.push({ unitId: unit.id, from: unit.coord, path: order.path, kind: order.kind, eta: null });
    }
  }
  return routes;
}

// Routes depend only on the static map, so they are cached per map for the page's
// life: re-planning one unit then costs one search, not one per unit.
const routeCache = new WeakMap<ColumnCosts, Map<string, Axial[] | null>>();

function cachedRoute(costs: ColumnCosts, from: Axial, to: Axial, via: readonly Axial[]): Axial[] | null {
  let cache = routeCache.get(costs);
  if (cache === undefined) {
    cache = new Map();
    routeCache.set(costs, cache);
  }
  const key = [from, ...via, to].map((a) => hexKey(a.q, a.r)).join(">");
  if (!cache.has(key)) cache.set(key, findRoute(costs, from, to, via));
  return cache.get(key) ?? null;
}

/** The map hexes within `radius` of `center`, as the 2-player reach helpers expect. */
function localIndex(map: WorldMap, center: Axial, radius: number): HexIndex {
  const index = new Map<string, Hex>();
  let frontier: Axial[] = [center];
  const seen = new Set([hexKey(center.q, center.r)]);
  for (let ring = 0; ring <= radius; ring++) {
    const next: Axial[] = [];
    for (const c of frontier) {
      const hex = map.hexAt(c.q, c.r);
      if (hex !== undefined) index.set(hexKey(c.q, c.r), hex);
      for (const n of axialNeighbors(c)) {
        const k = hexKey(n.q, n.r);
        if (!seen.has(k)) {
          seen.add(k);
          next.push(n);
        }
      }
    }
    frontier = next;
  }
  return index;
}
