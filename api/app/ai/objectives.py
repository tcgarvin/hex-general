"""Objectives: the strategic targets a bot assigns its units (AI_SPEC §4.2).

An `Objective` is a scored point of interest: a settlement to *capture* or
*defend*, or a position to *support* (artillery escorting a friendly effort).
The world bot (`app.ai.world`) assigns one to each unit, and the tactical layer
(`candidates`) uses its coord as the "advance toward" anchor for the unit's
candidate paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.engine.hex_coords import Axial


class ObjectiveKind(StrEnum):
    CAPTURE = "capture"
    DEFEND = "defend"
    SUPPORT = "support"  # artillery: escort the nearest friendly effort


@dataclass(frozen=True)
class Objective:
    """A scored point of interest on the map for the planning slot."""

    kind: ObjectiveKind
    coord: Axial
    value: float
