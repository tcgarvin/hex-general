// Planning a world turn (W6 slice 2): selection, board clicks, the staged plan and
// submission.
//
// The plan is keyed to the turn. Until the player edits, it *is* whatever the
// server holds for this turn (`view.submission`), so a reload or a second device
// shows the plan already in; editing forks a local copy, and a new turn drops it.
// A world player may resubmit until the clock resolves the turn, and each submit
// replaces the whole plan (WORLD_SPEC §10.1), so the local copy is always sent in
// full.
//
// Board clicks: one of your own units always selects it (a stack cycles), except
// Shift+click on a neighbouring deployed unit of yours, which orders the two to
// swap places (relief in place, `relief.ts`). Otherwise, in Travel mode, Shift+click
// adds a waypoint to the selected unit's travel (`waypoints.ts`); a plain click on
// a new destination starts a fresh route without waypoints. With a unit selected, Move and Barrage
// clicks outside the reach keep the selection and leave a `notice` saying why,
// rather than silently dropping it. Move reach is occupancy-aware
// (`moveRules.ts`), so a plan doesn't route through your own units or past an
// enemy.
//
// Unsent edits are mirrored into `sessionStorage` (`planStore.ts`) and restored
// after a reload of the same turn, but only over the server plan they were forked
// from: if a newer plan was submitted elsewhere, the draft is set aside and the
// player may take it back ("Use my draft instead").

import { useCallback, useEffect, useMemo, useState } from "react";

import { axialDistance, axialEqual, axialNeighbors, hexKey, type Axial, type Hex } from "@/board/hex";
import { api } from "@/lib/api/client";
import { describeApiError } from "@/lib/api/errors";
import { reachableContinuations, type HexIndex } from "@/planning/reach";
import { ARTILLERY_RANGE, isPassable, UNIT_MOVE } from "@/planning/rules";
import type { components } from "@/lib/api/schema";

import { contactSources, contactZone, runsIntoContact } from "./contact";
import type { Building } from "./economy";
import { hostileHexes, moveStepRules } from "./moveRules";
import {
  inBarrageRange,
  planFromSubmission,
  planKey,
  samePlan,
  submissionKey,
  toSubmission,
  type WorldOrder,
  type WorldPlan,
} from "./plan";
import { clearUnsentPlan, draftOnLoad, loadUnsentPlan, saveUnsentPlan, supersededBy } from "./planStore";
import { hostileZoc, swapBlocked, swapOrders, swapPartner } from "./relief";
import { columnCosts, findRoute, RouteSearch, travelEta, type ColumnCosts } from "./routing";
import { cycleUnit } from "./unitCycle";
import { addWaypoint, MAX_WAYPOINTS, removeWaypoint } from "./waypoints";
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
  /** The travel order's waypoints, in order (empty for anything else). */
  waypoints: readonly Axial[];
  /** The hostile-held hex a move ends on: the move is an attack. */
  attack: Axial | null;
}

/** A short message about a click that did nothing (`seq` makes repeats count). */
export interface Notice {
  text: string;
  seq: number;
}

/** The travel route a click on a hex would order, shown before the click. */
export interface TravelPreview {
  from: Axial;
  path: Axial[];
  eta: number;
  /** The route comes within auto-deploy distance of a hostile or a garrisoned site. */
  caution: boolean;
  /** The waypoints the route passes through (the hovered hex last, when previewing one). */
  waypoints: readonly Axial[];
  /** Previewing the hovered hex as the next waypoint (Shift held), not as a new destination. */
  asWaypoint: boolean;
}

/** Modifier keys held during a board click. */
export interface ClickMods {
  shift: boolean;
}

export const NOT_REACHABLE = "Not reachable this turn: use Travel for longer trips";
export const OUT_OF_RANGE = `Out of range: guns reach ${ARTILLERY_RANGE} hexes`;

export interface WorldPlanning {
  plan: WorldPlan;
  /** The local plan differs from what the server holds. */
  dirty: boolean;
  /** The server holds a plan for this turn. */
  submitted: boolean;
  /** The previous turn resolved while it had edits that were never sent. */
  droppedTurn: number | null;
  /** The plan shown was restored from this tab's storage after a reload, unsent. */
  restored: boolean;
  /**
   * This tab's unsent draft, set aside because a newer plan was submitted
   * elsewhere (another device or the API); the server's plan is shown instead.
   */
  setAside: boolean;
  /** Take the set-aside draft back as the unsent plan (it then replaces the server's on submit). */
  restoreSetAside: () => void;
  /** Forget the set-aside draft. */
  dismissSetAside: () => void;
  selectedUnit: Unit | null;
  selectedHex: Axial | null;
  mode: PlanMode;
  routes: readonly PlannedRoute[];
  /** Hexes a click would extend the selected unit's move to (move mode). */
  moveReach: ReadonlyMap<string, Axial[]>;
  /** Hexes holding a visible hostile unit (by `hexKey`): a move there is an attack. */
  hostile: ReadonlySet<string>;
  /** Hexes the selected artillery could shell (barrage mode). */
  barrageRange: readonly Axial[];
  notice: Notice | null;
  onHexClick: (hex: Axial, mods?: ClickMods) => void;
  selectUnit: (unitId: number | null) => void;
  /** Select the next (1) or previous (-1) own unit, idle ones first; returns it. */
  cycleUnit: (dir: 1 | -1) => Unit | null;
  /** Escape: leave Move or Barrage mode, else drop the selection. */
  cancel: () => void;
  /**
   * Search, without blocking the page, the travel route a click on `hex` would
   * order for the selected unit; `done` gets it (or `null`: no route). With
   * `asWaypoint` (Shift held), the route with `hex` added as the next waypoint of
   * the unit's travel. Returns a cancel function. The finished route is cached,
   * so the click reuses it.
   */
  previewTravel: (hex: Axial, done: (preview: TravelPreview | null) => void, asWaypoint?: boolean) => () => void;
  /** Drop the waypoint at `index` from the unit's travel order. */
  removeWaypoint: (unitId: number, index: number) => void;
  /** Drop all of the unit's travel waypoints. */
  clearWaypoints: (unitId: number) => void;
  setMode: (mode: PlanMode) => void;
  /** Set or clear a unit's order (clearing one half of a swap clears both). */
  setOrder: (unitId: number, order: WorldOrder | null) => void;
  /** Order the selected unit and its neighbour `friendId` to swap hexes (relief in place). */
  orderSwap: (friendId: number) => void;
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
  const serverKey = useMemo(() => submissionKey(view.submission, slot), [view.submission, slot]);
  // What a stored draft from this tab does on load: restored over the server
  // plan it was forked from, else set aside (`planStore.draftOnLoad`).
  const [onLoad] = useState(() => draftOnLoad(loadUnsentPlan(view.game_id, view.turn), serverKey));
  // `base`: the `submissionKey` of the server plan these edits were forked from.
  // `sent`: the server accepted this exact local plan (so a turn resolving under
  // it loses nothing).
  // `restored`: the plan came back from storage after a reload and is still unsent.
  const [local, setLocal] = useState<LocalPlan | null>(() =>
    onLoad.restore === null
      ? null
      : { turn: view.turn, plan: onLoad.restore, base: serverKey, sent: false, restored: true },
  );
  const [setAside, setSetAside] = useState<{ turn: number; plan: WorldPlan } | null>(() =>
    onLoad.setAside === null ? null : { turn: view.turn, plan: onLoad.setAside },
  );
  const current = local !== null && local.turn === view.turn ? local : null;
  const plan = current !== null ? current.plan : server;
  const droppedTurn = local !== null && local.turn < view.turn && !local.sent ? local.turn : null;
  const dirty = !samePlan(plan, server, slot);
  const restored = dirty && current !== null && current.restored;
  const asideNow = setAside !== null && setAside.turn === view.turn ? setAside : null;

  // A plan submitted elsewhere while this tab had unsent edits: set them aside
  // rather than let Resubmit quietly overwrite the newer plan. Checked when the
  // server's plan changes (state adjusted during render, not in an effect).
  const [seenKey, setSeenKey] = useState(serverKey);
  if (seenKey !== serverKey) {
    setSeenKey(serverKey);
    if (current !== null && supersededBy({ base: current.base, sent: current.sent, dirty }, serverKey)) {
      setSetAside({ turn: current.turn, plan: current.plan });
      setLocal(null);
    }
  }

  // Mirror unsent edits into this tab's storage; forget them once sent or undone
  // (a set-aside draft stays stored until the player decides).
  const gameId = view.game_id;
  const turn = view.turn;
  useEffect(() => {
    if (dirty && current !== null) saveUnsentPlan(gameId, turn, { plan, base: current.base });
    else if (asideNow !== null) saveUnsentPlan(gameId, turn, { plan: asideNow.plan, base: "" });
    else clearUnsentPlan(gameId, turn);
  }, [gameId, turn, plan, dirty, current, asideNow]);

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
  const hostile = useMemo(() => hostileHexes(state.units, slot), [state.units, slot]);
  const zone = useMemo(() => contactZone(contactSources(state, map, slot)), [state, map, slot]);
  const standing = useMemo(() => state.standing_orders ?? [], [state.standing_orders]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const warn = useCallback((text: string) => setNotice((n) => ({ text, seq: (n?.seq ?? 0) + 1 })), []);

  const edit = useCallback(
    (change: (p: WorldPlan) => WorldPlan) => {
      setLocal((prev) => {
        const mine = prev !== null && prev.turn === view.turn ? prev : null;
        return {
          turn: view.turn,
          plan: change(mine !== null ? mine.plan : server),
          base: mine !== null ? mine.base : serverKey,
          sent: false,
          restored: false,
        };
      });
    },
    [server, serverKey, view.turn],
  );

  const setOrder = useCallback(
    (unitId: number, order: WorldOrder | null) =>
      edit((p) => {
        const orders = { ...p.orders };
        // A swap is two orders that only work together: dropping or replacing
        // one half would leave the other walking into a hex that stays held.
        const unit = ownUnits.find((u) => u.id === unitId);
        const partner = unit === undefined ? null : swapPartner(unit, p.orders, ownUnits);
        if (partner !== null) delete orders[partner.id];
        if (order === null) delete orders[unitId];
        else orders[unitId] = order;
        return { ...p, orders };
      }),
    [edit, ownUnits],
  );

  const routes = useMemo(
    () => plannedRoutes(ownUnits, plan, standing, costs, hostile),
    [ownUnits, plan, standing, costs, hostile],
  );

  const moveReach = useMemo(() => {
    if (selectedUnit === null || sel.mode !== "move" || selectedUnit.posture === "column") return new Map<string, Axial[]>();
    const order = plan.orders[selectedUnit.id];
    const base = order?.kind === "move" ? order.path : [];
    const index = localIndex(map, selectedUnit.coord, UNIT_MOVE[selectedUnit.type] + 2);
    const rules = moveStepRules(selectedUnit, { units: state.units, viewer: slot, orders: plan.orders, standing });
    const reach = reachableContinuations(selectedUnit, [], index, rules);
    if (base.length > 0) for (const [k, v] of reachableContinuations(selectedUnit, base, index, rules)) reach.set(k, v);
    return reach;
  }, [selectedUnit, sel.mode, plan.orders, map, state.units, slot, standing]);

  const barrageRange = useMemo(() => {
    if (selectedUnit === null || sel.mode !== "barrage") return [];
    const { q, r } = selectedUnit.coord;
    const hexes: Axial[] = [];
    for (let dq = -ARTILLERY_RANGE; dq <= ARTILLERY_RANGE; dq++) {
      for (let dr = -ARTILLERY_RANGE; dr <= ARTILLERY_RANGE; dr++) {
        const at = { q: q + dq, r: r + dr };
        const d = axialDistance(at, selectedUnit.coord);
        if (d >= 1 && d <= ARTILLERY_RANGE && map.hexAt(at.q, at.r) !== undefined) hexes.push(at);
      }
    }
    return hexes;
  }, [selectedUnit, sel.mode, map]);

  const select = useCallback(
    (unitId: number | null, hex: Axial | null, mode: PlanMode = "travel") =>
      setSelection({ turn: view.turn, unitId, hex, mode }),
    [view.turn],
  );

  const zoc = useMemo(() => hostileZoc(state.units, slot), [state.units, slot]);
  const orderSwap = useCallback(
    (friendId: number) => {
      const friend = ownUnits.find((u) => u.id === friendId);
      if (selectedUnit === null || friend === undefined) return;
      const blocked = swapBlocked(selectedUnit, friend, zoc);
      if (blocked !== null) {
        warn(`Can't swap: ${blocked}`);
        return;
      }
      edit((p) => {
        const orders = { ...p.orders };
        // Each unit may be half of another swap already; that one is undone.
        for (const u of [selectedUnit, friend]) {
          const old = swapPartner(u, p.orders, ownUnits);
          if (old !== null) delete orders[old.id];
        }
        return { ...p, orders: { ...orders, ...swapOrders(selectedUnit, friend) } };
      });
    },
    [ownUnits, selectedUnit, zoc, warn, edit],
  );

  const onHexClick = useCallback(
    (hex: Axial, mods: ClickMods = { shift: false }) => {
      const key = hexKey(hex.q, hex.r);
      const here = ownUnits.filter((u) => u.coord.q === hex.q && u.coord.r === hex.r);
      const friend = here.find((u) => u.posture === "deployed");
      if (mods.shift && selectedUnit !== null) {
        // Precedence: a deployed neighbour of yours means a swap; otherwise, in
        // Travel mode, the hex becomes the next waypoint.
        const neighbour =
          friend !== undefined &&
          friend.id !== selectedUnit.id &&
          selectedUnit.posture === "deployed" &&
          axialDistance(friend.coord, selectedUnit.coord) === 1;
        if (neighbour) {
          orderSwap(friend.id);
          return;
        }
        if (sel.mode === "travel") {
          const base = travelBase(selectedUnit, plan.orders, standing);
          if (base === null) {
            warn("Set a destination first: click a hex, then Shift+click hexes to pass through");
            return;
          }
          const added = addWaypoint(
            (from, to, via) => cachedRoute(costs, from, to, via),
            map,
            selectedUnit.coord,
            base,
            hex,
          );
          if ("error" in added) warn(added.error);
          else setOrder(selectedUnit.id, { kind: "travel", destination: base.destination, via: added.via, onArrival: base.onArrival });
          return;
        }
        if (friend !== undefined && friend.id !== selectedUnit.id) {
          orderSwap(friend.id); // not a neighbour: says why it can't
          return;
        }
      }
      if (here.length > 0) {
        // Your own unit always selects, in every mode. Clicking a stack again
        // steps through its units, then the hex itself.
        const at = here.findIndex((u) => u.id === sel.unitId);
        select(at < here.length - 1 ? here[at + 1].id : null, hex);
        return;
      }
      if (selectedUnit !== null && sel.mode === "barrage") {
        if (inBarrageRange(selectedUnit, hex)) {
          setOrder(selectedUnit.id, { kind: "barrage", target: hex });
          select(selectedUnit.id, sel.hex, "travel");
        } else {
          warn(OUT_OF_RANGE);
        }
        return;
      }
      if (selectedUnit !== null && sel.mode === "move") {
        const path = moveReach.get(key);
        if (path !== undefined) setOrder(selectedUnit.id, { kind: "move", path });
        else warn(NOT_REACHABLE);
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
    [selectedUnit, sel, moveReach, ownUnits, map, costs, plan.orders, standing, setOrder, select, warn, orderSwap],
  );

  const previewTravel = useCallback(
    (hex: Axial, done: (preview: TravelPreview | null) => void, asWaypoint = false): (() => void) => {
      if (selectedUnit === null || axialEqual(hex, selectedUnit.coord)) {
        done(null);
        return () => {};
      }
      const cell = map.hexAt(hex.q, hex.r);
      if (cell === undefined || !isPassable(cell.terrain)) {
        done(null);
        return () => {};
      }
      const from = selectedUnit.coord;
      const packed = selectedUnit.posture === "column";
      // With Shift, the route as it would be with `hex` as the next waypoint.
      const base = asWaypoint ? travelBase(selectedUnit, plan.orders, standing) : null;
      if (asWaypoint && (base === null || base.via.length >= MAX_WAYPOINTS || axialEqual(hex, base.destination))) {
        done(null);
        return () => {};
      }
      const to = base !== null ? base.destination : hex;
      const via = base !== null ? [...base.via, hex] : [];
      const finish = (path: Axial[] | null) =>
        done(
          path === null
            ? null
            : {
                from,
                path,
                eta: travelEta(costs, path, { packed }),
                caution: runsIntoContact(from, path, zone),
                waypoints: via,
                asWaypoint: base !== null,
              },
        );
      return searchRoute(costs, from, to, via, finish);
    },
    [selectedUnit, map, costs, zone, plan.orders, standing],
  );

  const editVia = useCallback(
    (unitId: number, change: (via: readonly Axial[]) => Axial[]) =>
      edit((p) => {
        const order = p.orders[unitId];
        if (order?.kind !== "travel") return p;
        return { ...p, orders: { ...p.orders, [unitId]: { ...order, via: change(order.via) } } };
      }),
    [edit],
  );

  const busy = useCallback(
    (u: Unit) => plan.orders[u.id] !== undefined || standing.some((s) => s.unit_id === u.id),
    [plan.orders, standing],
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
        // The server now holds this plan: later edits fork from it.
        const base = planKey(plan, slot);
        setLocal((prev) =>
          prev !== null && prev.turn === turn && prev.plan === plan ? { ...prev, sent: true, base } : prev,
        );
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
    restored,
    setAside: asideNow !== null,
    restoreSetAside: () => {
      if (asideNow === null) return;
      setLocal({ turn: asideNow.turn, plan: asideNow.plan, base: serverKey, sent: false, restored: true });
      setSetAside(null);
    },
    dismissSetAside: () => setSetAside(null),
    selectedUnit,
    selectedHex: sel.hex,
    mode: selectedUnit === null ? "travel" : sel.mode,
    routes,
    moveReach,
    hostile,
    barrageRange,
    notice,
    onHexClick,
    selectUnit: (unitId) => {
      const unit = ownUnits.find((u) => u.id === unitId);
      select(unitId, unit?.coord ?? null);
    },
    cycleUnit: (dir) => {
      const next = cycleUnit(ownUnits, selectedUnit?.id ?? null, dir, busy);
      if (next !== null) select(next.id, next.coord);
      return next;
    },
    cancel: () => {
      if (selectedUnit !== null && sel.mode !== "travel") select(sel.unitId, sel.hex, "travel");
      else select(null, null);
    },
    previewTravel,
    removeWaypoint: (unitId, index) => editVia(unitId, (via) => removeWaypoint(via, index)),
    clearWaypoints: (unitId) => editVia(unitId, () => []),
    setMode: (mode) => select(sel.unitId, sel.hex, mode),
    setOrder,
    orderSwap,
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

/**
 * The travel a waypoint would join: the unit's staged Travel order, else the
 * standing order it is carrying on with (a waypoint then reissues it), else `null`.
 */
function travelBase(
  unit: Unit,
  orders: Readonly<Record<number, WorldOrder>>,
  standing: readonly components["schemas"]["StandingTravel"][],
): { destination: Axial; via: readonly Axial[]; onArrival: Posture } | null {
  const order = orders[unit.id];
  if (order?.kind === "travel") return order;
  if (order !== undefined) return null;
  const s = standing.find((t) => t.unit_id === unit.id);
  return s === undefined ? null : { destination: s.destination, via: [], onArrival: s.on_arrival ?? "deployed" };
}

/** This tab's edits to a turn's plan (see `useWorldPlan`). */
interface LocalPlan {
  turn: number;
  plan: WorldPlan;
  base: string;
  sent: boolean;
  restored: boolean;
}

/** Every own unit's path: fresh orders this turn, else the standing travel order. */
function plannedRoutes(
  units: readonly Unit[],
  plan: WorldPlan,
  standing: readonly components["schemas"]["StandingTravel"][],
  costs: ColumnCosts,
  hostile: ReadonlySet<string>,
): PlannedRoute[] {
  const routes: PlannedRoute[] = [];
  const standingBy = new Map(standing.map((s) => [s.unit_id, s]));
  for (const unit of units) {
    const order = plan.orders[unit.id];
    const packed = unit.posture === "column";
    if (order === undefined) {
      const s = standingBy.get(unit.id);
      if (s !== undefined) {
        routes.push({
          unitId: unit.id,
          from: unit.coord,
          path: s.route,
          kind: "standing",
          eta: travelEta(costs, s.route, { packed }),
          attack: null,
          waypoints: [],
        });
      }
    } else if (order.kind === "travel") {
      const path = cachedRoute(costs, unit.coord, order.destination, order.via);
      // The server re-checks reachability; a staged order always had a route when set.
      if (path === null) throw new Error(`staged travel for unit ${unit.id} has no route`);
      routes.push({
        unitId: unit.id,
        from: unit.coord,
        path,
        kind: "travel",
        eta: travelEta(costs, path, { packed }),
        attack: null,
        waypoints: order.via,
      });
    } else if (order.kind === "move" || order.kind === "march") {
      // Only a deployed Move can attack; a column halts on contact instead.
      const end = order.path[order.path.length - 1];
      const attack = order.kind === "move" && end !== undefined && hostile.has(hexKey(end.q, end.r)) ? end : null;
      routes.push({ unitId: unit.id, from: unit.coord, path: order.path, kind: order.kind, eta: null, attack, waypoints: [] });
    }
  }
  return routes;
}

// Routes depend only on the static map, so they are cached per map for the page's
// life: re-planning one unit then costs one search, not one per unit.
const routeCache = new WeakMap<ColumnCosts, Map<string, Axial[] | null>>();

function cacheFor(costs: ColumnCosts): Map<string, Axial[] | null> {
  let cache = routeCache.get(costs);
  if (cache === undefined) {
    cache = new Map();
    routeCache.set(costs, cache);
  }
  // Hover previews search a route per hex the pointer rests on; keep the cache bounded.
  if (cache.size > 4000) cache.clear();
  return cache;
}

const routeKey = (from: Axial, to: Axial, via: readonly Axial[]) => [from, ...via, to].map((a) => hexKey(a.q, a.r)).join(">");

function cachedRoute(costs: ColumnCosts, from: Axial, to: Axial, via: readonly Axial[]): Axial[] | null {
  const cache = cacheFor(costs);
  const key = routeKey(from, to, via);
  if (!cache.has(key)) cache.set(key, findRoute(costs, from, to, via));
  return cache.get(key) ?? null;
}

/** Main-thread time per slice of a background route search, leaving room for a frame. */
const SLICE_MS = 8;

/**
 * Find the route `from` → `to` in slices between frames, so a far destination on a
 * big map never stalls the page; `done` gets it (once, unless cancelled first).
 * Same search and tie-breaks as `findRoute`, and the result goes into the cache.
 */
function searchRoute(
  costs: ColumnCosts,
  from: Axial,
  to: Axial,
  via: readonly Axial[],
  done: (path: Axial[] | null) => void,
): () => void {
  const key = routeKey(from, to, via);
  const cache = cacheFor(costs);
  if (cache.has(key)) {
    done(cache.get(key) ?? null);
    return () => {};
  }
  const search = new RouteSearch(costs, from, to, via);
  let timer: ReturnType<typeof setTimeout> | null = null;
  const slice = () => {
    const until = performance.now() + SLICE_MS;
    if (search.advance(() => performance.now() > until)) {
      timer = null;
      cacheFor(costs).set(key, search.result);
      done(search.result);
    } else {
      timer = setTimeout(slice, 0);
    }
  };
  slice();
  return () => {
    if (timer !== null) clearTimeout(timer);
  };
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
