"""Tests for line-of-sight and per-player visibility (SPEC §9, roadmap P6)."""

from app.engine.hex_coords import Axial
from app.engine.visibility import (
    has_line_of_sight,
    unit_visible_hexes,
    visible_hexes,
)
from engine.helpers import build


def test_line_of_sight_to_self_is_clear() -> None:
    state = build(["....."], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    assert has_line_of_sight(state, Axial(q=0, r=0), Axial(q=0, r=0))


def test_unit_sees_within_vision_radius_on_open_ground() -> None:
    # Vision 2 (SPEC §9.3): on plains the unit sees its hex and the two beyond.
    state = build(["....."], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    unit = state.units[0]
    seen = unit_visible_hexes(state, unit)
    assert Axial(q=0, r=0) in seen
    assert Axial(q=1, r=0) in seen
    assert Axial(q=2, r=0) in seen
    # The fourth hex is out of vision range.
    assert Axial(q=3, r=0) not in seen


def test_woods_block_sight_beyond_but_the_woods_hex_is_seen() -> None:
    # Woods one hex away (SPEC §9.2): the woods hex is visible, the hex behind it
    # is occluded.
    state = build([".w..."], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    unit = state.units[0]
    seen = unit_visible_hexes(state, unit)
    assert Axial(q=1, r=0) in seen  # the woods hex itself
    assert Axial(q=2, r=0) not in seen  # blocked by the woods


def test_unit_standing_in_woods_still_sees_out() -> None:
    # The observer's own terrain never blocks its sight.
    state = build(["w...."], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    seen = unit_visible_hexes(state, state.units[0])
    assert Axial(q=1, r=0) in seen
    assert Axial(q=2, r=0) in seen


def test_visible_hexes_unions_over_all_a_players_units() -> None:
    # Two units far apart each contribute their own field of view.
    state = build(
        [".........."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 8, "r": 0},
        ],
    )
    seen = visible_hexes(state, "blue")
    assert Axial(q=0, r=0) in seen
    assert Axial(q=8, r=0) in seen
    # The gap between them is unseen by either unit.
    assert Axial(q=4, r=0) not in seen


def test_visibility_is_per_player() -> None:
    state = build(
        ["....."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 4, "r": 0},
        ],
    )
    blue_seen = visible_hexes(state, "blue")
    red_seen = visible_hexes(state, "red")
    assert Axial(q=0, r=0) in blue_seen
    assert Axial(q=0, r=0) not in red_seen
    assert Axial(q=4, r=0) in red_seen
    assert Axial(q=4, r=0) not in blue_seen


def test_player_with_no_units_sees_nothing() -> None:
    state = build(
        ["....."],
        [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}],
        players=["blue", "red"],
    )
    assert visible_hexes(state, "red") == set()
