"""Tests for the axial/cube hex coordinate library (roadmap P0)."""

from app.engine.hex_coords import (
    AXIAL_DIRECTIONS,
    Axial,
    Cube,
    add,
    distance,
    from_cube,
    is_adjacent,
    neighbor,
    neighbors,
    to_cube,
)


def test_axial_is_frozen_and_hashable() -> None:
    a = Axial(q=2, r=-3)
    # Equal coordinates are equal and share a hash, so they work as keys.
    assert a == Axial(q=2, r=-3)
    assert {a, Axial(q=2, r=-3)} == {a}


def test_cube_conversion_round_trips() -> None:
    for q in range(-3, 4):
        for r in range(-3, 4):
            a = Axial(q=q, r=r)
            assert from_cube(to_cube(a)) == a


def test_cube_constraint_holds() -> None:
    c = to_cube(Axial(q=4, r=-1))
    assert c.x + c.y + c.z == 0


def test_neighbors_count_and_distance() -> None:
    origin = Axial(q=0, r=0)
    ns = neighbors(origin)
    assert len(ns) == 6
    assert len(set(ns)) == 6
    assert all(distance(origin, n) == 1 for n in ns)


def test_neighbor_matches_direction_table() -> None:
    origin = Axial(q=5, r=5)
    for i, direction in enumerate(AXIAL_DIRECTIONS):
        assert neighbor(origin, i) == add(origin, direction)


def test_neighbor_rejects_bad_direction() -> None:
    try:
        neighbor(Axial(q=0, r=0), 6)
    except ValueError:
        pass
    else:  # pragma: no cover - failure path
        raise AssertionError("expected ValueError for out-of-range direction")


def test_distance_is_symmetric_and_zero_on_self() -> None:
    a = Axial(q=1, r=2)
    b = Axial(q=-2, r=3)
    assert distance(a, a) == 0
    assert distance(a, b) == distance(b, a)


def test_distance_known_values() -> None:
    # Two steps along one axis, then a diagonal that the cube metric counts as 1.
    assert distance(Axial(q=0, r=0), Axial(q=3, r=0)) == 3
    assert distance(Axial(q=0, r=0), Axial(q=0, r=-2)) == 2
    assert distance(Axial(q=0, r=0), Axial(q=2, r=-1)) == 2


def test_is_adjacent() -> None:
    origin = Axial(q=0, r=0)
    assert is_adjacent(origin, neighbor(origin, 0))
    assert not is_adjacent(origin, origin)
    assert not is_adjacent(origin, Axial(q=2, r=0))


def test_coords_serialize_to_json() -> None:
    a = Axial(q=3, r=-4)
    assert Axial.model_validate_json(a.model_dump_json()) == a
    c = Cube(x=1, y=-1, z=0)
    assert Cube.model_validate_json(c.model_dump_json()) == c
