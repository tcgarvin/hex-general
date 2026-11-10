"""Terrain rules table: lake is open water, impassable and never bridged."""

from __future__ import annotations

from app.engine.stats import TERRAIN_STATS, Terrain


def test_lake_is_impassable_open_water() -> None:
    stats = TERRAIN_STATS[Terrain.LAKE]
    assert not stats.passable
    assert stats.defense_bonus == 0
    assert not stats.blocks_line_of_sight


def test_every_terrain_has_stats() -> None:
    assert set(TERRAIN_STATS) == set(Terrain)
