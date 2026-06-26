// The planning state machine for a player's turn (roadmap Phase 9). It owns the
// selected unit and one staged *order* per unit — move (an exact path), barrage
// (a target hex), rest, or hold — resolves board clicks into the right action for
// the current interaction mode, and projects the staged orders into the `Order`
// union for `POST /games/{id}/orders`.
//
// Slice 1 covered movement; slice 2 adds the barrage/rest/hold palette. Move stays
// the default interaction (path drawn on the board); barrage is a transient
// targeting mode; rest/hold are bare orders set from the panel. State is keyed to
// the turn: when the turn advances (the engine resolved and a fresh planning phase
// begins) the selection and all staged orders reset.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { axialEqual, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { barrageTargets, reachableContinuations } from "./reach";

type GameState = components["schemas"]["GameState"];
type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];
type UnitType = components["schemas"]["UnitType"];
type Recruitment = components["schemas"]["Recruitment"];
type Order =
  | components["schemas"]["MoveOrder"]
  | components["schemas"]["BarrageOrder"]
  | components["schemas"]["RestOrder"]
  | components["schemas"]["HoldOrder"];

/** A bare (target-less) order a unit can be given from the panel. */
export type BareOrderKind = "rest" | "hold";

/** A staged recruit at one friendly city, before projection to a wire `Recruitment`. */
export interface StagedRecruit {
  coord: Axial;
  type: UnitType;
}

/** A staged order for one unit, before projection to the wire `Order` union. */
export type StagedOrder =
  | { kind: "move"; path: Axial[] }
  | { kind: "barrage"; target: Axial }
  | { kind: "rest" }
  | { kind: "hold" };

/** What a board click means for the selected unit. */
export type OrderMode = "move" | "barrage";

/** The slice of planning state the board needs to render and drive clicks. */
export interface PlanningController {
  selectedUnitId: number | null;
  /** Interaction mode for the selected unit (move path vs. barrage targeting). */
  mode: OrderMode;
  /** Staged orders, keyed by unit id. Absent ⇒ no order for that unit. */
  stagedByUnit: Readonly<Record<number, StagedOrder>>;
  /** Hexes the selected artillery may barrage (empty unless aiming a barrage). */
  barrageTargetKeys: ReadonlySet<string>;
  onHexClick: (coord: Axial) => void;
  /** Select a unit (move mode) or clear the selection — used by the roster panel. */
  selectUnit: (unitId: number | null) => void;
}

/** A staged order paired with its unit, surfaced for the review panel. */
export interface StagedEntry {
  unit: Unit;
  order: StagedOrder;
}

export interface Planning extends PlanningController {
  selectedUnit: Unit | null;
  /** The friendly city selected for recruitment, or null (mutually exclusive with a unit). */
  selectedCity: Axial | null;
  /** Staged recruits, keyed by city hex key. At most one per city (SPEC §6.5). */
  recruitBySite: Readonly<Record<string, StagedRecruit>>;
  /** Whether `coord` is a recruitable city: a controlled, empty city hex. */
  isRecruitCity: (coord: Axial) => boolean;
  /** Select a friendly city for recruitment (or clear with null). */
  selectCity: (coord: Axial | null) => void;
  /** Stage (or replace) the recruit at a city. */
  stageRecruit: (coord: Axial, type: UnitType) => void;
  /** Remove the staged recruit at a city. */
  clearRecruit: (coord: Axial) => void;
  staged: StagedEntry[];
  /** The `Order`s to submit (one per staged order). */
  orders: Order[];
  /** The `Recruitment`s to submit (one per staged recruit). */
  recruitments: Recruitment[];
  /** Select a unit in move mode (board clicks draw its path). */
  enterMove: (unitId: number) => void;
  /** Select a unit and aim a barrage (no-op if the unit is not artillery). */
  enterBarrage: (unitId: number) => void;
  /** Stage a bare rest/hold order, replacing any existing order for the unit. */
  setBareOrder: (unitId: number, kind: BareOrderKind) => void;
  clearOrder: (unitId: number) => void;
  clearAll: () => void;
}

/** The selected unit's move path, or [] if it has no staged move. */
function movePathOf(order: StagedOrder | undefined): Axial[] {
  return order !== undefined && order.kind === "move" ? order.path : [];
}

export function usePlanning(state: GameState, yourSlot: string, turn: number): Planning {
  const [selectedUnitId, setSelectedUnitId] = useState<number | null>(null);
  const [selectedCity, setSelectedCity] = useState<Axial | null>(null);
  const [mode, setMode] = useState<OrderMode>("move");
  const [stagedByUnit, setStagedByUnit] = useState<Record<number, StagedOrder>>({});
  const [recruitBySite, setRecruitBySite] = useState<Record<string, StagedRecruit>>({});

  // A new turn means a fresh planning phase — drop any selection and staged plan.
  const lastTurn = useRef(turn);
  useEffect(() => {
    if (lastTurn.current !== turn) {
      lastTurn.current = turn;
      setSelectedUnitId(null);
      setSelectedCity(null);
      setMode("move");
      setStagedByUnit({});
      setRecruitBySite({});
    }
  }, [turn]);

  const units = useMemo(() => state.units ?? [], [state.units]);

  const unitsById = useMemo(() => {
    const m = new Map<number, Unit>();
    for (const u of units) m.set(u.id, u);
    return m;
  }, [units]);

  const hexes = useMemo<ReadonlyMap<string, Hex>>(() => {
    const m = new Map<string, Hex>();
    for (const h of state.map.hexes ?? []) m.set(hexKey(h.coord.q, h.coord.r), h);
    return m;
  }, [state.map.hexes]);

  const ownUnitAt = useCallback(
    (coord: Axial): Unit | null =>
      units.find((u) => u.owner === yourSlot && axialEqual(u.coord, coord)) ?? null,
    [units, yourSlot],
  );

  // Settlement control by hex, so a click can tell "your city" from neutral/enemy.
  const controlAt = useMemo<ReadonlyMap<string, string | null>>(() => {
    const m = new Map<string, string | null>();
    for (const s of state.settlements ?? []) {
      m.set(hexKey(s.coord.q, s.coord.r), s.controlled_by ?? null);
    }
    return m;
  }, [state.settlements]);

  // A recruitable hex is a city you control with no unit currently on it. An
  // occupied city selects its unit instead — the unit can move off and the city
  // becomes recruitable next click. The server re-checks emptiness end-of-turn.
  const isRecruitCity = useCallback(
    (coord: Axial): boolean => {
      const key = hexKey(coord.q, coord.r);
      const hex = hexes.get(key);
      if (hex === undefined || hex.terrain !== "city") return false;
      if (controlAt.get(key) !== yourSlot) return false;
      return !units.some((u) => axialEqual(u.coord, coord));
    },
    [hexes, controlAt, yourSlot, units],
  );

  /** Set or clear a unit's staged order (undefined removes it). */
  const setOrder = useCallback((unitId: number, order: StagedOrder | undefined) => {
    setStagedByUnit((prev) => {
      const next = { ...prev };
      if (order === undefined) delete next[unitId];
      else next[unitId] = order;
      return next;
    });
  }, []);

  /** Set or clear a unit's move path (an empty path removes the order). */
  const setMovePath = useCallback(
    (unitId: number, path: Axial[]) =>
      setOrder(unitId, path.length === 0 ? undefined : { kind: "move", path }),
    [setOrder],
  );

  const selectUnit = useCallback((unitId: number | null) => {
    setSelectedUnitId(unitId);
    setSelectedCity(null);
    setMode("move");
  }, []);

  const selectCity = useCallback((coord: Axial | null) => {
    setSelectedCity(coord);
    setSelectedUnitId(null);
    setMode("move");
  }, []);

  const stageRecruit = useCallback((coord: Axial, type: UnitType) => {
    setRecruitBySite((prev) => ({ ...prev, [hexKey(coord.q, coord.r)]: { coord, type } }));
  }, []);

  const clearRecruit = useCallback((coord: Axial) => {
    setRecruitBySite((prev) => {
      const next = { ...prev };
      delete next[hexKey(coord.q, coord.r)];
      return next;
    });
  }, []);

  const selectedUnit =
    selectedUnitId === null ? null : (unitsById.get(selectedUnitId) ?? null);

  // The selected artillery's barrage range, only while actually aiming a barrage.
  const barrageTargetKeys = useMemo<ReadonlySet<string>>(() => {
    if (mode !== "barrage" || selectedUnit === null) return new Set();
    return barrageTargets(selectedUnit, hexes);
  }, [mode, selectedUnit, hexes]);

  const onHexClick = useCallback(
    (coord: Axial) => {
      if (selectedUnitId === null) {
        const unit = ownUnitAt(coord);
        if (unit !== null) {
          selectUnit(unit.id);
          return;
        }
        // A controlled, empty city: select it to recruit (click again to deselect).
        if (isRecruitCity(coord)) {
          const same = selectedCity !== null && axialEqual(selectedCity, coord);
          selectCity(same ? null : coord);
          return;
        }
        if (selectedCity !== null) selectCity(null);
        return;
      }

      const unit = unitsById.get(selectedUnitId);
      if (unit === undefined) {
        selectUnit(null);
        return;
      }

      // Barrage targeting: click an in-range hex to fire; clicking the unit or an
      // out-of-range hex cancels back to move mode without staging.
      if (mode === "barrage") {
        if (
          !axialEqual(unit.coord, coord) &&
          barrageTargets(unit, hexes).has(hexKey(coord.q, coord.r))
        ) {
          setOrder(selectedUnitId, { kind: "barrage", target: coord });
        }
        setMode("move");
        return;
      }

      // Move mode: build the selected unit's exact path.
      const path = movePathOf(stagedByUnit[selectedUnitId]);

      // Click the unit's own hex: clear its path, or deselect if already empty.
      if (axialEqual(unit.coord, coord)) {
        if (path.length > 0) setMovePath(selectedUnitId, []);
        else selectUnit(null);
        return;
      }
      // Click the current destination to undo the last step.
      if (path.length > 0 && axialEqual(path[path.length - 1], coord)) {
        setMovePath(selectedUnitId, path.slice(0, -1));
        return;
      }
      // Click any reachable hex to route to it (commits the previewed path).
      const route = reachableContinuations(unit, path, hexes).get(hexKey(coord.q, coord.r));
      if (route !== undefined) {
        setMovePath(selectedUnitId, route);
        return;
      }
      // Otherwise: switch to another of your units, a recruit city, or cancel.
      const other = ownUnitAt(coord);
      if (other !== null) {
        selectUnit(other.id);
        return;
      }
      if (isRecruitCity(coord)) {
        selectCity(coord);
        return;
      }
      selectUnit(null);
    },
    [selectedUnitId, mode, unitsById, stagedByUnit, hexes, ownUnitAt, selectUnit, setOrder, setMovePath, isRecruitCity, selectCity, selectedCity],
  );

  const enterMove = useCallback((unitId: number) => selectUnit(unitId), [selectUnit]);

  const enterBarrage = useCallback(
    (unitId: number) => {
      const unit = unitsById.get(unitId);
      if (unit === undefined || unit.type !== "artillery") return;
      setSelectedUnitId(unitId);
      setMode("barrage");
    },
    [unitsById],
  );

  const setBareOrder = useCallback(
    (unitId: number, kind: BareOrderKind) => {
      setOrder(unitId, { kind });
      if (unitId === selectedUnitId) setMode("move");
    },
    [setOrder, selectedUnitId],
  );

  const clearOrder = useCallback(
    (unitId: number) => {
      setOrder(unitId, undefined);
      if (unitId === selectedUnitId) setMode("move");
    },
    [setOrder, selectedUnitId],
  );

  const clearAll = useCallback(() => {
    setStagedByUnit({});
    setRecruitBySite({});
    setSelectedUnitId(null);
    setSelectedCity(null);
    setMode("move");
  }, []);

  const staged = useMemo<StagedEntry[]>(() => {
    const out: StagedEntry[] = [];
    for (const [id, order] of Object.entries(stagedByUnit)) {
      const unit = unitsById.get(Number(id));
      if (unit !== undefined) out.push({ unit, order });
    }
    return out;
  }, [stagedByUnit, unitsById]);

  const orders = useMemo<Order[]>(
    () =>
      staged.map(({ unit, order }): Order => {
        const unit_id = unit.id;
        switch (order.kind) {
          case "move":
            return { kind: "move", unit_id, path: order.path };
          case "barrage":
            return { kind: "barrage", unit_id, target: order.target };
          case "rest":
            return { kind: "rest", unit_id };
          case "hold":
            return { kind: "hold", unit_id };
        }
      }),
    [staged],
  );

  const recruitments = useMemo<Recruitment[]>(
    () =>
      Object.values(recruitBySite).map(
        (r): Recruitment => ({ owner: yourSlot, coord: r.coord, type: r.type }),
      ),
    [recruitBySite, yourSlot],
  );

  return {
    selectedUnitId,
    mode,
    stagedByUnit,
    barrageTargetKeys,
    onHexClick,
    selectUnit,
    selectedUnit,
    selectedCity,
    recruitBySite,
    isRecruitCity,
    selectCity,
    stageRecruit,
    clearRecruit,
    staged,
    orders,
    recruitments,
    enterMove,
    enterBarrage,
    setBareOrder,
    clearOrder,
    clearAll,
  };
}
