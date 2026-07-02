"""Per-turn board indexes and shared board queries (AI_SPEC §3, §8).

The AI's one performance rule is "build coord-keyed dicts once per ``plan()``;
never call `GameState.hex_at`/`unit_at` in loops" (AI_SPEC §8). `BoardIndex` is
that shared, read-only index: hex-by-coord, unit-by-coord, settlement-by-coord,
plus derived rosters (own/enemy units, controlled settlements). Every policy
layer builds one `BoardIndex` per turn and passes it down, so no layer re-scans
the 1600-hex map.

This is an internal helper (not one of AI_SPEC §3's named modules); it exists
purely to satisfy the §8 indexing rule in one place instead of duplicating the
scans across `baselines`/`objectives`/`candidates`. It follows the repo's engine
standard: it trusts its already-validated `GameState` and indexes directly.
"""

from __future__ import annotations

from app.engine.hex_coords import Axial, distance
from app.engine.models import GameState, Hex, PlayerId, Settlement, Unit
from app.engine.stats import TERRAIN_STATS, UNIT_STATS, Terrain


class BoardIndex:
    """Coord-keyed indexes over a single (redacted) `GameState` for one turn."""

    def __init__(self, view: GameState, slot: PlayerId) -> None:
        self.view = view
        self.slot = slot
        self.hex_by_coord: dict[Axial, Hex] = {h.coord: h for h in view.map.hexes}
        self.unit_by_coord: dict[Axial, Unit] = {u.coord: u for u in view.units}
        self.unit_by_id: dict[int, Unit] = {u.id: u for u in view.units}
        self.settlement_by_coord: dict[Axial, Settlement] = {
            s.coord: s for s in view.settlements
        }
        self.own_units: list[Unit] = [u for u in view.units if u.owner == slot]
        self.enemy_units: list[Unit] = [u for u in view.units if u.owner != slot]

    def terrain_at(self, coord: Axial) -> Terrain:
        """Terrain of an on-map hex (raises for off-map — a caller bug)."""
        return self.hex_by_coord[coord].terrain

    def passable(self, coord: Axial) -> bool:
        """Whether ``coord`` is on-map and passable terrain (SPEC §3.4)."""
        hex_ = self.hex_by_coord.get(coord)
        return hex_ is not None and TERRAIN_STATS[hex_.terrain].passable

    def is_free(self, coord: Axial) -> bool:
        """Whether ``coord`` is passable and unoccupied (for recruit/reposition)."""
        return self.passable(coord) and coord not in self.unit_by_coord

    def controlled_settlements(self, owner: PlayerId) -> list[Settlement]:
        """Settlements ``owner`` currently controls."""
        return [s for s in self.view.settlements if s.controlled_by == owner]

    def city_coords(self, owner: PlayerId) -> list[Axial]:
        """Coords of cities ``owner`` controls (recruitment sites, SPEC §6.5)."""
        return [
            s.coord
            for s in self.view.settlements
            if s.controlled_by == owner
            and self.hex_by_coord[s.coord].terrain is Terrain.CITY
        ]

    def city_count(self, owner: PlayerId) -> int:
        """Number of cities ``owner`` controls (city-elimination logic, §15.1)."""
        return len(
            [
                s
                for s in self.view.settlements
                if s.controlled_by == owner
                and self.hex_by_coord[s.coord].terrain is Terrain.CITY
            ]
        )

    def move_allowance(self, unit: Unit) -> int:
        """Base move allowance for a unit's type (SPEC §5.3); road +1 handled in
        pathfinding where the whole-path road test can be applied."""
        return UNIT_STATS[unit.type].move

    def enemies_within(self, coord: Axial, radius: int) -> list[Unit]:
        """Enemy units within ``radius`` hexes of ``coord`` (visible only, by fog)."""
        return [u for u in self.enemy_units if distance(u.coord, coord) <= radius]

    def own_units_within(self, coord: Axial, radius: int) -> list[Unit]:
        """Own units within ``radius`` hexes of ``coord``."""
        return [u for u in self.own_units if distance(u.coord, coord) <= radius]

    def adjacent_enemy(self, coord: Axial, owner: PlayerId) -> bool:
        """Whether any neighbour of ``coord`` holds an enemy of ``owner`` (ZOC)."""
        for other in self.view.units:
            if other.owner != owner and distance(other.coord, coord) == 1:
                return True
        return False


def is_combat_unit(unit: Unit) -> bool:
    """Whether a unit can capture settlements (infantry/armor, SPEC §14.1)."""
    return UNIT_STATS[unit.type].can_capture


def unit_material(unit: Unit) -> float:
    """Cost-weighted material value of a unit at its current HP (AI_SPEC §4.3)."""
    return UNIT_STATS[unit.type].recruit_cost * unit.hp / 10.0
