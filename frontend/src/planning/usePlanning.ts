// The planning state machine for a player's turn (roadmap Phase 9, Slice 1:
// movement orders). It owns the selected unit and a staged exact path per unit,
// resolves board clicks into select / extend / trim / switch / deselect, and
// projects the staged paths into `MoveOrder`s for `POST /games/{id}/orders`.
//
// State is keyed to the turn: when the turn advances (the engine resolved and a
// fresh planning phase begins) the selection and all staged paths reset.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { axialEqual, hexKey, type Axial } from "@/board/hex";
import type { components } from "@/lib/api/schema";

import { canExtendPath } from "./reach";

type GameState = components["schemas"]["GameState"];
type Hex = components["schemas"]["Hex"];
type Unit = components["schemas"]["Unit"];
type MoveOrder = components["schemas"]["MoveOrder"];

/** The slice of planning state the board needs to render and drive clicks. */
export interface PlanningController {
  selectedUnitId: number | null;
  /** Staged exact paths, keyed by unit id. Absent ⇒ no order for that unit. */
  paths: Readonly<Record<number, Axial[]>>;
  onHexClick: (coord: Axial) => void;
}

/** A staged move, surfaced for the review panel. */
export interface StagedMove {
  unit: Unit;
  path: Axial[];
}

export interface Planning extends PlanningController {
  selectedUnit: Unit | null;
  staged: StagedMove[];
  /** The `MoveOrder`s to submit (one per staged path). */
  orders: MoveOrder[];
  clearOrder: (unitId: number) => void;
  clearAll: () => void;
}

export function usePlanning(state: GameState, yourSlot: string, turn: number): Planning {
  const [selectedUnitId, setSelectedUnitId] = useState<number | null>(null);
  const [paths, setPaths] = useState<Record<number, Axial[]>>({});

  // A new turn means a fresh planning phase — drop any selection and staged paths.
  const lastTurn = useRef(turn);
  useEffect(() => {
    if (lastTurn.current !== turn) {
      lastTurn.current = turn;
      setSelectedUnitId(null);
      setPaths({});
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

  /** Set or clear a unit's staged path (an empty path removes the order). */
  const setPath = useCallback((unitId: number, path: Axial[]) => {
    setPaths((prev) => {
      const next = { ...prev };
      if (path.length === 0) delete next[unitId];
      else next[unitId] = path;
      return next;
    });
  }, []);

  const onHexClick = useCallback(
    (coord: Axial) => {
      if (selectedUnitId === null) {
        const unit = ownUnitAt(coord);
        if (unit !== null) setSelectedUnitId(unit.id);
        return;
      }

      const unit = unitsById.get(selectedUnitId);
      if (unit === undefined) {
        setSelectedUnitId(null);
        return;
      }
      const path = paths[selectedUnitId] ?? [];

      // Click the path's last step to undo it.
      if (path.length > 0 && axialEqual(path[path.length - 1], coord)) {
        setPath(selectedUnitId, path.slice(0, -1));
        return;
      }
      // Click the unit's own hex to deselect it.
      if (axialEqual(unit.coord, coord)) {
        setSelectedUnitId(null);
        return;
      }
      // Extend the path if this is a legal next step.
      if (canExtendPath(unit, path, coord, hexes)) {
        setPath(selectedUnitId, [...path, coord]);
        return;
      }
      // Otherwise: switch to another of your units, or cancel the selection.
      const other = ownUnitAt(coord);
      setSelectedUnitId(other?.id ?? null);
    },
    [selectedUnitId, unitsById, paths, hexes, ownUnitAt, setPath],
  );

  const clearOrder = useCallback((unitId: number) => setPath(unitId, []), [setPath]);
  const clearAll = useCallback(() => {
    setPaths({});
    setSelectedUnitId(null);
  }, []);

  const selectedUnit =
    selectedUnitId === null ? null : (unitsById.get(selectedUnitId) ?? null);

  const staged = useMemo<StagedMove[]>(() => {
    const out: StagedMove[] = [];
    for (const [id, path] of Object.entries(paths)) {
      const unit = unitsById.get(Number(id));
      if (unit !== undefined && path.length > 0) out.push({ unit, path });
    }
    return out;
  }, [paths, unitsById]);

  const orders = useMemo<MoveOrder[]>(
    () => staged.map(({ unit, path }) => ({ kind: "move", unit_id: unit.id, path })),
    [staged],
  );

  return {
    selectedUnitId,
    paths,
    onHexClick,
    selectedUnit,
    staged,
    orders,
    clearOrder,
    clearAll,
  };
}
