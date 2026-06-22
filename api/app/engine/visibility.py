"""Line-of-sight and per-player visibility (SPEC §9; roadmap Phase 6).

Fog of war is enforced server-side: the API hands each player a *filtered* view of
the game so the browser never receives hidden enemy units (CLAUDE.md, roadmap
decision 4). This module is the geometry half of that — the pure question "which
hexes can a player see?" — with no notion of how the answer is applied. The
redaction half (using a visible set to drop hidden units and unwitnessed events)
lives in `app.engine.fog`.

Visibility in v0.1 is binary (SPEC §9.1): a hex is either visible or not, with no
concealment state. A player sees a hex if any of their units has line of sight to
it within that unit's vision radius (SPEC §9.3). Woods block line of sight (SPEC
§9.2): a sightline is clear only if no hex strictly between the observer and the
target is woods — the observer still sees *into* an adjacent or unobstructed woods
hex (the blocking hex itself is visible), but not through it.

All functions are pure queries over a `GameState`; they never mutate it.
"""

from __future__ import annotations

from app.engine.hex_coords import Axial, distance, line
from app.engine.models import GameState, PlayerId, Unit
from app.engine.stats import TERRAIN_STATS, UNIT_STATS


def has_line_of_sight(state: GameState, source: Axial, target: Axial) -> bool:
    """Return whether ``source`` has a clear sightline to ``target`` (SPEC §9.2).

    The line is blocked iff some hex *strictly between* the two endpoints blocks
    line of sight (woods). The endpoints themselves never block: an observer sees
    out of its own woods hex, and sees into a woods hex it is looking directly at.
    Both endpoints are assumed on-map; a hex line between two on-map coordinates
    stays on-map, so the intermediate lookups are trusted to resolve.
    """
    if source == target:
        return True
    sightline = line(source, target)
    for coord in sightline[1:-1]:
        between = state.hex_at(coord)
        if between is not None and TERRAIN_STATS[between.terrain].blocks_line_of_sight:
            return False
    return True


def unit_visible_hexes(state: GameState, unit: Unit) -> set[Axial]:
    """Return the set of hexes ``unit`` can see (SPEC §9.2–9.3).

    Every on-map hex within the unit's vision radius to which it has a clear
    sightline, always including the unit's own hex.
    """
    vision = UNIT_STATS[unit.type].vision
    visible: set[Axial] = set()
    for hex_ in state.map.hexes:
        coord = hex_.coord
        if distance(unit.coord, coord) <= vision and has_line_of_sight(
            state, unit.coord, coord
        ):
            visible.add(coord)
    return visible


def visible_hexes(state: GameState, player_id: PlayerId) -> set[Axial]:
    """Return the union of all hexes ``player_id``'s units can see (SPEC §9).

    A player with no surviving units sees nothing (an empty set), which is the
    correct fog result — they get no information about the board.
    """
    visible: set[Axial] = set()
    for unit in state.units:
        if unit.owner == player_id:
            visible |= unit_visible_hexes(state, unit)
    return visible
