"""Per-turn board indexes and shared board queries (AI_SPEC §3, §8).

The AI's one performance rule is "build coord-keyed dicts once per ``plan()``;
never call `GameState.hex_at`/`unit_at` in loops" (AI_SPEC §8). `BoardIndex` is
that shared, read-only index: hex-by-coord, unit-by-coord, settlement-by-coord,
plus derived rosters (own/enemy units, controlled settlements). Every policy
layer builds one `BoardIndex` per turn and passes it down, so no layer re-scans
the 1600-hex map.

This is an internal helper (not one of AI_SPEC §3's named modules); it exists
purely to satisfy the §8 indexing rule in one place instead of duplicating the
scans across `candidates`/`recruit`/`world`. It follows the repo's engine
standard: it trusts its already-validated `GameState` and indexes directly.
"""

from __future__ import annotations

from functools import cached_property

from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import (
    NEUTRAL,
    GameState,
    Hex,
    PlayerId,
    Posture,
    Settlement,
    Unit,
)
from app.engine.stats import (
    SETTLEMENT_DEFENSE_BONUS,
    SETTLEMENT_TERRAINS,
    TERRAIN_STATS,
    UNIT_STATS,
    Terrain,
)


class BoardIndex:
    """Coord-keyed indexes over a single (redacted) `GameState` for one turn."""

    def __init__(self, view: GameState, slot: PlayerId) -> None:
        self.view = view
        self.slot = slot
        # The map's own cached index: a world map is ~10^5 hexes, too many to
        # re-index on every plan() (WORLD_SPEC §10).
        self.hex_by_coord: dict[Axial, Hex] = view.map.index
        # Deployed units win a shared hex: packed columns stack under them
        # (WORLD_SPEC §4.2), and the deployed unit is the one that blocks/fights.
        self.unit_by_coord: dict[Axial, Unit] = {
            u.coord: u
            for u in sorted(view.units, key=lambda u: u.posture is Posture.DEPLOYED)
        }
        self.settlement_by_coord: dict[Axial, Settlement] = {
            s.coord: s for s in view.settlements
        }
        self.own_units: list[Unit] = [u for u in view.units if u.owner == slot]
        self.enemy_units: list[Unit] = [u for u in view.units if u.owner != slot]

    @cached_property
    def player_contact(self) -> frozenset[Axial]:
        """Hexes beside a deployed unit of another player (garrisons excluded)."""
        return frozenset(
            near
            for u in self.enemy_units
            if u.owner != NEUTRAL and u.posture is Posture.DEPLOYED
            for near in neighbors(u.coord)
        )

    def terrain_at(self, coord: Axial) -> Terrain:
        """Terrain of an on-map hex (raises for off-map — a caller bug)."""
        return self.hex_by_coord[coord].terrain

    def passable(self, coord: Axial) -> bool:
        """Whether ``coord`` is on-map and passable terrain (SPEC §3.4)."""
        hex_ = self.hex_by_coord.get(coord)
        return hex_ is not None and TERRAIN_STATS[hex_.terrain].passable

    def rest_ground(self, coord: Axial) -> bool:
        """`GameState.settlement_ground` from the indexes: town or city terrain, or
        a site hex whose settlement exists (where resting heals fastest)."""
        if self.hex_by_coord[coord].terrain in SETTLEMENT_TERRAINS:
            return True
        return coord in self.view.map.site_index and coord in self.settlement_by_coord

    def terrain_defense(self, coord: Axial) -> int:
        """`GameState.terrain_defense` from the indexes (no settlement scan)."""
        bonus = TERRAIN_STATS[self.hex_by_coord[coord].terrain].defense_bonus
        if self.rest_ground(coord):
            return max(bonus, SETTLEMENT_DEFENSE_BONUS)
        return bonus

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
        """Whether any neighbour of ``coord`` holds a deployed enemy of ``owner``
        (ZOC; packed columns exert none, WORLD_SPEC §4.2)."""
        for near in neighbors(coord):
            other = self.unit_by_coord.get(near)
            if (
                other is not None
                and other.owner != owner
                and other.posture is Posture.DEPLOYED
            ):
                return True
        return False


def is_combat_unit(unit: Unit) -> bool:
    """Whether a unit can capture settlements (infantry/armor, SPEC §14.1)."""
    return UNIT_STATS[unit.type].can_capture
