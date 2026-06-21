"""Axial hex coordinates with cube conversion (SPEC §3, roadmap decision 3).

The map uses **axial** coordinates ``(q, r)`` for storage and ``Cube`` for the
math that is awkward in axial space (distance, and later line-of-sight). Both
coordinate types are frozen Pydantic models so they are hashable — usable as
``dict``/``set`` keys at runtime — while still serializing cleanly to JSON.

Orientation is pointy-top; the six neighbour directions are fixed and ordered,
which matters because deterministic execution depends on stable ordering.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Axial(BaseModel):
    """An axial hex coordinate.

    ``q`` is the column-ish axis and ``r`` the row-ish axis. Frozen so two
    coordinates with the same values are equal and hashable.
    """

    model_config = ConfigDict(frozen=True)

    q: int
    r: int


class Cube(BaseModel):
    """A cube hex coordinate with the constraint ``x + y + z == 0``.

    Used for distance and interpolation; convert to/from `Axial` with
    `to_cube` / `from_cube`.
    """

    model_config = ConfigDict(frozen=True)

    x: int
    y: int
    z: int


# Pointy-top neighbour offsets in axial space, in a fixed clockwise-ish order.
# Index this list to refer to a specific direction; the order is load-bearing
# for deterministic tie-breaking later in the engine.
AXIAL_DIRECTIONS: tuple[Axial, ...] = (
    Axial(q=1, r=0),
    Axial(q=1, r=-1),
    Axial(q=0, r=-1),
    Axial(q=-1, r=0),
    Axial(q=-1, r=1),
    Axial(q=0, r=1),
)


def to_cube(a: Axial) -> Cube:
    """Convert an axial coordinate to its cube representation."""
    x = a.q
    z = a.r
    y = -x - z
    return Cube(x=x, y=y, z=z)


def from_cube(c: Cube) -> Axial:
    """Convert a cube coordinate back to axial."""
    return Axial(q=c.x, r=c.z)


def add(a: Axial, b: Axial) -> Axial:
    """Return the component-wise sum of two axial coordinates."""
    return Axial(q=a.q + b.q, r=a.r + b.r)


def neighbor(a: Axial, direction: int) -> Axial:
    """Return the neighbour of ``a`` in the given direction index (0..5)."""
    if not 0 <= direction < len(AXIAL_DIRECTIONS):
        raise ValueError(
            f"direction must be in 0..{len(AXIAL_DIRECTIONS) - 1}, got {direction}"
        )
    return add(a, AXIAL_DIRECTIONS[direction])


def neighbors(a: Axial) -> list[Axial]:
    """Return all six neighbours of ``a`` in fixed direction order."""
    return [add(a, d) for d in AXIAL_DIRECTIONS]


def distance(a: Axial, b: Axial) -> int:
    """Return the hex distance (number of steps) between two coordinates."""
    dq = a.q - b.q
    dr = a.r - b.r
    return (abs(dq) + abs(dq + dr) + abs(dr)) // 2


def is_adjacent(a: Axial, b: Axial) -> bool:
    """Return whether two distinct coordinates share an edge."""
    return distance(a, b) == 1
