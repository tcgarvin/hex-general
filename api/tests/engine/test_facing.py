"""Tests for cosmetic unit facing (roadmap Phase 8 slice U0; SPEC render aid).

Facing is left/right only, driven by the unit's final completed move step of the
turn on the flat-top board's ``q`` axis; fresh units (recruits, turn-0) face the
nearest enemy. These pin the derivation, the preserve-prior-facing corners, and
backward-compatible loading of state persisted before the field existed.
"""

from __future__ import annotations

from app.engine.models import (
    DEFAULT_FACING,
    Facing,
    GameState,
    Unit,
    default_facing,
)
from app.engine.movement import resolve_movement
from app.engine.recruitment import resolve_recruitment
from engine.helpers import ax, build, move, recruit, rest


def _facing(state: GameState, unit_id: int) -> Facing:
    return next(u.facing for u in state.units if u.id == unit_id)


# --- Movement drives facing --------------------------------------------------


def test_facing_follows_rightward_movement() -> None:
    # Lone blue infantry (no enemies) loads with the default facing, then walks
    # east — its final step is rightward, so it ends facing RIGHT.
    state = build(["....."], [{"type": "infantry", "owner": "blue", "q": 0, "r": 0}])
    assert _facing(state, 1) == DEFAULT_FACING
    new_state, _ = resolve_movement(state, [move(1, (1, 0), (2, 0))])
    assert _facing(new_state, 1) == Facing.RIGHT


def test_facing_reflects_final_step_not_first() -> None:
    # Path goes right then ends with a leftward step: the last step wins → LEFT.
    state = build(["....."], [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}])
    new_state, _ = resolve_movement(state, [move(1, (2, 0), (1, 0))])
    assert new_state.unit_at(ax(1, 0)) is not None
    assert _facing(new_state, 1) == Facing.LEFT


def test_vertical_final_step_preserves_prior_facing() -> None:
    # Start facing LEFT (enemy-less scenario forces default RIGHT, so drive it
    # LEFT via a leftward step), then a purely vertical step (same q) keeps LEFT.
    state = build(["..", ".."], [{"type": "infantry", "owner": "blue", "q": 1, "r": 0}])
    new_state, _ = resolve_movement(state, [move(1, (0, 0), (0, 1))])
    assert new_state.unit_at(ax(0, 1)) is not None
    assert _facing(new_state, 1) == Facing.LEFT


def test_rest_order_preserves_prior_facing() -> None:
    # Blue at (2,0) faces the red enemy on its left (LEFT); a Rest order never
    # moves the unit, so it keeps that facing.
    state = build(
        ["..."],
        [
            {"type": "infantry", "owner": "red", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},  # id 2
        ],
    )
    assert _facing(state, 2) == Facing.LEFT
    new_state, _ = resolve_movement(state, [rest(2)])
    assert _facing(new_state, 2) == Facing.LEFT


def test_blocked_move_preserves_prior_facing() -> None:
    # Blue at (1,0) faces LEFT (enemy on its left). Its rightward move into the
    # impassable river never completes, so a mere *attempt* rightward does not
    # flip facing — it stays LEFT.
    state = build(
        ["..~"],
        [
            {"type": "infantry", "owner": "red", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},  # id 2
        ],
    )
    assert _facing(state, 2) == Facing.LEFT
    new_state, _ = resolve_movement(state, [move(2, (2, 0))])
    assert new_state.unit_at(ax(1, 0)) is not None  # did not move
    assert _facing(new_state, 2) == Facing.LEFT


def test_combat_entrant_faces_its_entering_step() -> None:
    # Blue armor at (1,0) starts facing LEFT (nearest enemy by (distance, id) is
    # the red unit on its left). It attacks the 1-HP red unit on its right,
    # destroys it, and enters (2,0) — a rightward step that flips facing to RIGHT.
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 1, "r": 0},  # id 1
            {"type": "infantry", "owner": "red", "q": 0, "r": 0},  # id 2
            {"type": "infantry", "owner": "red", "q": 2, "r": 0, "hp": 1},  # id 3
        ],
    )
    assert _facing(state, 1) == Facing.LEFT
    new_state, _ = resolve_movement(state, [move(1, (2, 0))])
    assert new_state.unit_at(ax(2, 0)) is not None
    assert new_state.unit_at(ax(2, 0)).id == 1  # type: ignore[union-attr]
    assert _facing(new_state, 1) == Facing.RIGHT


# --- Fresh units face the nearest enemy --------------------------------------


def test_recruit_faces_nearest_enemy() -> None:
    # City at (2,0); the only enemy is to the left, so the recruit faces LEFT.
    state = build(
        ["..c"],
        [{"type": "infantry", "owner": "red", "q": 0, "r": 0}],
        settlements=[{"q": 2, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 10}, "red"],
    )
    new_state, _ = resolve_recruitment(state, [recruit("blue", "infantry", 2, 0)])
    recruited = new_state.unit_at(ax(2, 0))
    assert recruited is not None
    assert recruited.owner == "blue"
    assert recruited.facing == Facing.LEFT


def test_recruit_with_no_enemy_uses_default_facing() -> None:
    # No enemy units on the board → the recruit takes the enum default.
    state = build(
        ["..c"],
        [],
        settlements=[{"q": 2, "r": 0, "owner": "blue"}],
        players=[{"id": "blue", "bank": 10}],
    )
    new_state, _ = resolve_recruitment(state, [recruit("blue", "infantry", 2, 0)])
    recruited = new_state.unit_at(ax(2, 0))
    assert recruited is not None
    assert recruited.facing == DEFAULT_FACING


def test_scenario_turn_zero_units_face_the_enemy() -> None:
    # Blue on the left, red on the right — each faces the other across the board.
    state = build(
        ["....."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "red", "q": 4, "r": 0},  # id 2
        ],
    )
    assert _facing(state, 1) == Facing.RIGHT  # blue faces right toward red
    assert _facing(state, 2) == Facing.LEFT  # red faces left toward blue


def test_default_facing_helper_tie_breaks_on_lowest_id() -> None:
    # Two equidistant enemies (both distance 1): the lower id wins the tie, and it
    # sits to the left, so the unit faces LEFT.
    left = Unit(id=2, type="infantry", owner="red", coord=ax(0, 0), hp=10)
    right = Unit(id=3, type="infantry", owner="red", coord=ax(2, 0), hp=10)
    assert default_facing(ax(1, 0), "blue", [right, left]) == Facing.LEFT


# --- Backward compatibility --------------------------------------------------


def test_unit_without_facing_validates_with_default() -> None:
    unit = Unit.model_validate(
        {
            "id": 1,
            "type": "infantry",
            "owner": "blue",
            "coord": {"q": 0, "r": 0},
            "hp": 10,
        }
    )
    assert unit.facing == DEFAULT_FACING
