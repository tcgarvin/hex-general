"""Operations: a strategist's war plan against one neighbour, as plain data.

An `Operation` is what the strategist hands the tactical layer when it goes to
war (AI_SPEC §12.1): whom it fights, the settlement it means to take, where the
force assembles on its own side of the border, and how many combat units it
commits. The tactical layer executes it (`app.ai.world`): the force gathers at
the staging area, advances together once assembled, closes up out of reach of
the defenders, and assaults the objective with several units in the same turn.

`plan_operation` is the scripted strategist's way of choosing one. Nothing
survives between ticks on the live server (the world tick builds a fresh
`WorldPolicy` each turn, `app.games.service`), so the operation is derived from
the fog-redacted view alone and made sticky by where the bot's units already
are: a settlement the bot's units already stand around stays the objective, and
a war the bot's units are already fighting goes on even when the army has
shrunk below what it takes to start one. A later strategist with a persistent
notebook (VISION §9) can hold an operation explicitly instead.

It lives in its own module so that `BotPlan` can report the operation a bot ran
(the world simulator measures operations from it) without importing the world
bot.
"""

from __future__ import annotations

import math
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from app.engine.economy_rules import SiteKind
from app.engine.hex_coords import Axial, distance, line, within
from app.engine.models import NEUTRAL, GameState, PlayerId, Unit
from app.engine.stats import TERRAIN_STATS, UNIT_STATS


class Operation(BaseModel):
    """One offensive against one hostile player.

    ``objective`` is a settlement ``target`` holds. ``staging`` is the hex the
    force assembles around before it advances: an own holding near the
    objective, else a hex on the way there a few hexes short of it. ``force``
    is the number of combat units committed; ``assemble`` is how many of them
    must stand ready near the objective before the advance starts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target: PlayerId
    objective: Axial
    staging: Axial
    force: int = Field(ge=2)
    assemble: int = Field(ge=2)


# Combat units a bot wants before it starts a war: from `_WAR_ARMY_MAX` for the
# most cautious bot down to `_WAR_ARMY_MIN` for the boldest.
_WAR_ARMY_MAX = 12
_WAR_ARMY_MIN = 8
# A war under way goes on while the army is at most this many short of it.
_WAR_HYSTERESIS = 3
# The fewest free combat units (not kept home) worth an operation; below this
# the war stops and the bot consolidates.
_MIN_FORCE = 3
# Share of free combat units committed: from `_SHARE_MIN` (cautious) up to
# `_SHARE_MIN + _SHARE_SPAN` (bold). The rest keep expanding and defending.
_SHARE_MIN = 0.6
_SHARE_SPAN = 0.3
# The force must reach `_STRIKE_ODDS` times the target's units in view within
# `_DEFENDED_RADIUS` of the objective (the tactical layer strikes at similar
# odds, `app.ai.world._ODDS`).
_STRIKE_ODDS = 1.2
_DEFENDED_RADIUS = 3
# Hostile units this close to the staging area put the whole free army in.
_CONTESTED_RADIUS = 6
# Share of the force that must stand ready before it advances (at least
# `_MIN_FORCE`): waiting for every last unit leaves the rest idle for turns.
_ASSEMBLE_SHARE = 0.6
# A hostile neighbour counts when it holds a settlement this far beyond the
# bot's reach from the nearest own holding.
_TARGET_SLACK = 6
# Own combat units within this many hexes of a hostile settlement mean the war
# for it is already under way (the hysteresis that makes an operation sticky).
_ENGAGED_RADIUS = 7
_ENGAGED_UNITS = 2
# Hexes of preference per own combat unit already engaged at a settlement: the
# more of the army is in a war, the harder it is to turn it elsewhere.
_STICKY_PER_UNIT = 3
_STICKY_BONUS = 8  # hexes of preference for a clearly beaten neighbour
_RETALIATE_BONUS = 2  # hexes of preference for a neighbour whose units are at our land
_BASE_BONUS = 10  # hexes of preference for the enemy base once clearly winning
# A bot clearly beats a neighbour when it holds `_WINNING_RATIO` times its
# settlements and at least `_WINNING_MARGIN` more: it presses on to the base.
_WINNING_RATIO = 2.0
_WINNING_MARGIN = 3
# While cheap neutral land is in reach (``expanding``) the only war is against
# a clearly beaten neighbour, with this share of the usual force: wars of
# choice while land is cheap cost 15-25% of the neutral captures (AI_SPEC
# §12.4).
_EXPANDING_SHARE = 0.4
# Staging: an own holding at most this far from the objective, else the
# passable hex nearest the point `_STAGE_GAP` short of it on the straight line
# there (searched within `_STAGE_SEARCH`).
_STAGE_HOLDING_MAX = 8
_STAGE_GAP = 5
_STAGE_SEARCH = 2
_THREAT_RADIUS = 8
_HOME_DANGER_RADIUS = 6  # hostile units this close to the base put home in danger
_HOME_DANGER_UNITS = 2
_NO_HOSTILE = 10**6


def plan_operation(
    view: GameState,
    slot: PlayerId,
    aggression: float,
    reach: int,
    reserve_share: float,
    expanding: bool = False,
) -> Operation | None:
    """The offensive ``slot`` should run this turn, or ``None`` for none.

    A bot goes to war when its army reaches its war size (by ``aggression``)
    and a hostile player holds a settlement within ``reach`` (+ slack) of its
    own holdings, unless it is still ``expanding`` (cheap neutral land in
    reach). A war already under way goes on with a smaller army, and one
    against a neighbour the bot clearly beats (`_WINNING_RATIO` times its
    settlements) runs even while expanding, while at least `_MIN_FORCE`
    combat units are free. A war is under way at a hostile settlement when
    own combat units stand around it or march to a staging area near it
    (standing travel orders persist between turns, so they are the bot's
    memory of its plan). No operation runs while the home base is in danger.

    The target is the nearest such neighbour, preferring one already being
    fought (the more units engaged, the stronger the preference), clearly
    beaten, or whose units stand on the bot's land. The objective is its
    settlement nearest the bot's holdings, preferring the one the force
    already stands at, and its base once the bot clearly beats it; one whose
    visible defence the free army cannot match at `_STRIKE_ODDS` is passed
    over, and with none left the bot runs no operation.
    """
    held = [s.coord for s in view.settlements if s.controlled_by == slot]
    if not held:
        return None
    combat = [
        u for u in view.units if u.owner == slot and UNIT_STATS[u.type].can_capture
    ]
    free = len(combat) - int(reserve_share * len(combat))
    if free < _MIN_FORCE:
        return None
    threats = [
        u for u in view.units if u.owner != NEUTRAL and view.hostile(u.owner, slot)
    ]
    if _home_in_danger(view, slot, threats):
        return None
    candidates: dict[PlayerId, list[Axial]] = {}
    for settlement in view.settlements:
        owner = settlement.controlled_by
        if owner is None or not view.hostile(owner, slot):
            continue
        if _nearest(settlement.coord, held) <= reach + _TARGET_SLACK:
            candidates.setdefault(owner, []).append(settlement.coord)
    settlements = {s.coord for s in view.settlements}
    heading = {
        o.unit_id: o.destination
        for o in view.standing_orders
        if o.destination in held or o.destination not in settlements
    }
    own_ground = set(held)
    engaged: dict[Axial, int] = {}
    for coords in candidates.values():
        for c in coords:
            count = sum(
                1
                for u in combat
                if _engaged_at(u.coord, heading.get(u.id), c, own_ground)
            )
            if count >= _ENGAGED_UNITS:
                engaged[c] = count
    holdings = Counter(s.controlled_by for s in view.settlements if s.controlled_by)
    beaten = {
        p
        for p in candidates
        if len(held) >= _WINNING_RATIO * holdings[p]
        and len(held) - holdings[p] >= _WINNING_MARGIN
    }
    war_army = round(_WAR_ARMY_MAX - (_WAR_ARMY_MAX - _WAR_ARMY_MIN) * aggression)
    open_war: dict[PlayerId, list[Axial]] = {}
    for player, coords in candidates.items():
        # A war already under way there keeps going on a smaller army than it
        # takes to start one; where to fight never relaxes.
        keep = [
            c
            for c in coords
            if player in beaten
            or (
                not expanding
                and len(combat) >= war_army - _WAR_HYSTERESIS * (c in engaged)
            )
        ]
        if keep:
            open_war[player] = keep
    if not open_war:
        return None
    candidates = open_war
    target = _pick_target(candidates, held, engaged, beaten, threats)
    share = _SHARE_MIN + _SHARE_SPAN * aggression
    if expanding:
        share *= _EXPANDING_SHARE
    base_force = max(_MIN_FORCE, min(free, round(share * free)))
    for objective in _rank_objectives(view, target, candidates[target], held, engaged):
        # Match the force to the defence in view: enough to strike at
        # `_STRIKE_ODDS`. An objective the free army can't match is passed
        # over for the next; with none left the bot stands on the defensive.
        defenders = sum(
            1
            for u in threats
            if u.owner == target and distance(u.coord, objective) <= _DEFENDED_RADIUS
        )
        needed = math.ceil(_STRIKE_ODDS * defenders)
        if needed > free:
            continue
        staging = _staging(view, objective, held)
        force = max(base_force, needed)
        if any(distance(u.coord, staging) <= _CONTESTED_RADIUS for u in threats):
            # The enemy is already at the staging area: commit more, all of
            # the free army once there is no cheap land left to take.
            force = max(force, min(free, base_force + 1) if expanding else free)
        assemble = max(
            min(force, needed),
            min(force, max(_MIN_FORCE, math.ceil(_ASSEMBLE_SHARE * force))),
        )
        return Operation(
            target=target,
            objective=objective,
            staging=staging,
            force=force,
            assemble=assemble,
        )
    return None


def _engaged_at(
    where: Axial, heading: Axial | None, coord: Axial, staging_points: set[Axial]
) -> bool:
    """Whether a unit at ``where`` (marching to ``heading``, if any) is already
    in the war for the settlement at ``coord``.

    A march counts only when it looks like one to a staging area: to an own
    holding near ``coord``, or to open ground a few hexes short of it. A march
    to a neutral site is expansion, not war.
    """
    if distance(where, coord) <= _ENGAGED_RADIUS:
        return True
    if heading is None:
        return False
    gap = distance(heading, coord)
    if heading in staging_points:
        return gap <= _STAGE_HOLDING_MAX
    return gap <= _STAGE_GAP + 1


def _home_in_danger(view: GameState, slot: PlayerId, threats: list[Unit]) -> bool:
    """Whether hostile units stand near the bot's base in strength."""
    sites = view.map.site_index
    bases = [
        s.coord
        for s in view.settlements
        if s.controlled_by == slot
        and s.coord in sites
        and sites[s.coord].kind is SiteKind.BASE
    ]
    return any(
        sum(1 for u in threats if distance(u.coord, b) <= _HOME_DANGER_RADIUS)
        >= _HOME_DANGER_UNITS
        for b in bases
    )


def _pick_target(
    candidates: dict[PlayerId, list[Axial]],
    held: list[Axial],
    engaged: dict[Axial, int],
    beaten: set[PlayerId],
    threats: list[Unit],
) -> PlayerId:
    """The neighbour to fight: nearest, unless one is already being fought, is
    clearly beaten, or has units on the bot's land."""

    def key(player: PlayerId) -> tuple[int, PlayerId]:
        coords = candidates[player]
        gap = min(_nearest(c, held) for c in coords)
        gap -= _STICKY_PER_UNIT * max((engaged.get(c, 0) for c in coords), default=0)
        if player in beaten:
            gap -= _STICKY_BONUS
        if any(
            u.owner == player and _nearest(u.coord, held) <= _THREAT_RADIUS
            for u in threats
        ):
            gap -= _RETALIATE_BONUS
        return gap, player

    return min(candidates, key=key)


def _rank_objectives(
    view: GameState,
    target: PlayerId,
    coords: list[Axial],
    held: list[Axial],
    engaged: dict[Axial, int],
) -> list[Axial]:
    """The target's settlements, best objective first: nearest the bot's
    holdings, the one the force already stands at, the target's base once the
    bot clearly beats it."""
    sites = view.map.site_index
    theirs = sum(1 for s in view.settlements if s.controlled_by == target)
    winning = len(held) >= _WINNING_RATIO * theirs

    def key(coord: Axial) -> tuple[int, int, int]:
        gap = _nearest(coord, held)
        gap -= _STICKY_PER_UNIT * engaged.get(coord, 0)
        if winning and coord in sites and sites[coord].kind is SiteKind.BASE:
            gap -= _BASE_BONUS
        return gap, coord.q, coord.r

    return sorted(coords, key=key)


def _staging(view: GameState, objective: Axial, held: list[Axial]) -> Axial:
    """Where the force assembles: the own holding nearest the objective when it
    is close enough to strike from, else the passable hex nearest the point
    `_STAGE_GAP` hexes short of the objective on the straight line from that
    holding (no routing: a column route on a world map can take seconds)."""
    home = min(held, key=lambda h: (distance(h, objective), h.q, h.r))
    if distance(home, objective) <= _STAGE_HOLDING_MAX:
        return home
    path = line(home, objective)
    point = path[len(path) - 1 - _STAGE_GAP]
    index = view.map.index
    spots = [
        c
        for c in within(point, _STAGE_SEARCH)
        if c in index and TERRAIN_STATS[index[c].terrain].passable
    ]
    if not spots:
        return home
    return min(spots, key=lambda c: (distance(c, point), c.q, c.r))


def _nearest(coord: Axial, others: list[Axial]) -> int:
    """Hex distance from ``coord`` to the nearest of ``others`` (huge if none)."""
    return min((distance(coord, o) for o in others), default=_NO_HOSTILE)
