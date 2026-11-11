"""What a world bot knows this turn, read from its fog-redacted view only.

A bot plans from `redact_state(state, slot)`, so it sees its own units, hostile
units inside its vision, and everything public: the map, site kinds and garrison
tiers (`MapSite.garrison`), and who controls each settlement. `Intel` turns that
into the facts the tactical layer keeps asking for:

* **Threats** are visible units of other *players*. Neutral garrisons always hold
  and never move, so they strike only a unit that attacks them; they make no hex
  dangerous to stand in.
* **Garrisons** are what the bot expects to fight at each neutral site. A site in
  view shows its garrison exactly (the visible neutral units around it). A site
  out of view is assumed to keep its whole tier (`GARRISON_FORCES`): garrisons
  never respawn or move, and the bot can't know what others did to it.
* **Danger spots** are where a packed column would run into a garrison and
  auto-deploy disordered (WORLD_SPEC §4.5): every visible neutral unit, and every
  unseen neutral site still garrisoned. Travel planning stops short of them.

Nothing here reads hidden state, so it is fog-honest by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from app.engine.economy_rules import GARRISON_FORCES
from app.engine.hex_coords import Axial, distance, within
from app.engine.models import NEUTRAL, GameState, PlayerId, Unit
from app.engine.stats import COLUMN_RULES
from app.engine.visibility import visible_hexes

# A garrison's units stand on its site and the nearest open hexes (up to 3 out).
_GARRISON_SPREAD = 3
# Coarse buckets for the danger-spot lookup: a spot's radius never exceeds this.
_BUCKET = 8
_NO_THREAT = 10**6


@dataclass(frozen=True)
class Garrison:
    """The neutral force a bot expects at one site (see the module docstring).

    ``hp`` is the garrison's total HP; ``units`` the garrison units in view (empty
    when the site is out of view, ``seen`` False).
    """

    site: Axial
    hp: int
    seen: bool
    units: tuple[Unit, ...]

    @property
    def holds_site(self) -> bool:
        """Whether a garrison unit still stands on the site hex (blocking capture)."""
        if not self.seen:
            return self.hp > 0
        return any(u.coord == self.site for u in self.units)


@dataclass(frozen=True)
class Box:
    """An axial bounding box, a cheap prefilter before exact distance checks.

    A hex within ``margin`` of one of the ``around`` coords has its q and r each
    within ``margin`` of that coord, so it lies inside the box.
    """

    q0: int
    q1: int
    r0: int
    r1: int

    @classmethod
    def around(cls, coords: list[Axial], margin: int) -> Box:
        """The box holding every hex within ``margin`` of any of ``coords``."""
        if not coords:
            return cls(0, -1, 0, -1)  # empty
        return cls(
            min(c.q for c in coords) - margin,
            max(c.q for c in coords) + margin,
            min(c.r for c in coords) - margin,
            max(c.r for c in coords) + margin,
        )

    def __contains__(self, coord: Axial) -> bool:
        return self.q0 <= coord.q <= self.q1 and self.r0 <= coord.r <= self.r1


class Intel:
    """Fog-honest facts about one bot's view, built once per turn.

    ``area`` bounds the bot's planning: garrisons and danger spots are worked
    out only inside it (a world has thousands of sites).
    """

    def __init__(self, view: GameState, slot: PlayerId, area: Box) -> None:
        self.view = view
        self.slot = slot
        self.area = area
        self.visible: set[Axial] = visible_hexes(view, slot)
        self.threats: list[Unit] = self.player_threats(view, slot)
        self.garrisons: dict[Axial, Garrison] = self._garrisons()

    @staticmethod
    def player_threats(view: GameState, slot: PlayerId) -> list[Unit]:
        """Visible units of players hostile to ``slot`` (garrisons excluded)."""
        return [
            u for u in view.units if u.owner != NEUTRAL and view.hostile(u.owner, slot)
        ]

    def _garrisons(self) -> dict[Axial, Garrison]:
        sites = self.view.map.site_index
        neutral_sites = {
            s.coord
            for s in self.view.settlements
            if s.controlled_by is None
            and s.coord in self.area
            and sites[s.coord].garrison > 0
        }
        seen_units: dict[Axial, list[Unit]] = {}
        for unit in self.view.units:
            if unit.owner != NEUTRAL:
                continue
            near = [
                c for c in within(unit.coord, _GARRISON_SPREAD) if c in neutral_sites
            ]
            if near:
                home = min(near, key=lambda c: (distance(c, unit.coord), c.q, c.r))
                seen_units.setdefault(home, []).append(unit)
        garrisons: dict[Axial, Garrison] = {}
        for coord in neutral_sites:
            if coord in self.visible:
                units = tuple(seen_units.get(coord, ()))
                garrisons[coord] = Garrison(
                    coord, sum(u.hp for u in units), True, units
                )
            else:
                tier = GARRISON_FORCES[sites[coord].garrison]
                garrisons[coord] = Garrison(coord, sum(hp for _, hp in tier), False, ())
        return garrisons

    def threat_distance(self, coord: Axial) -> int:
        """Hex distance to the nearest visible player threat (huge if none)."""
        return min((distance(coord, u.coord) for u in self.threats), default=_NO_THREAT)

    def threats_within(self, coord: Axial, radius: int) -> list[Unit]:
        """Visible player threats within ``radius`` hexes of ``coord``."""
        return [u for u in self.threats if distance(u.coord, coord) <= radius]

    @cached_property
    def _danger_buckets(self) -> dict[tuple[int, int], list[tuple[Axial, int]]]:
        """Danger spots (coord, radius) bucketed by coarse position.

        A column halts when its next step would come within the deploy distance
        of a hostile unit, so a hex is unsafe within that distance of a garrison
        unit. An unseen site's extra units may stand a hex or two off the site, so
        tier 3–4 sites get one more hex.
        """
        reach = COLUMN_RULES.deploy_distance
        spots: list[tuple[Axial, int]] = [
            (u.coord, reach)
            for u in self.view.units
            if u.owner == NEUTRAL and u.coord in self.area
        ]
        for garrison in self.garrisons.values():
            if not garrison.seen:
                extra = 1 if garrison.hp > 10 else 0
                spots.append((garrison.site, reach + extra))
        buckets: dict[tuple[int, int], list[tuple[Axial, int]]] = {}
        for coord, radius in spots:
            key = (coord.q // _BUCKET, coord.r // _BUCKET)
            buckets.setdefault(key, []).append((coord, radius))
        return buckets

    def corridor_clear(self, start: Axial, goal: Axial, slack: int) -> bool:
        """Whether no danger spot lies anywhere near the way from ``start`` to
        ``goal``: within ``slack`` hexes (plus its radius) of the detour through it.

        A cheap test before routing: a route seldom strays far from the straight
        line, so with no spot in the corridor a column can travel straight there.
        """
        direct = distance(start, goal)
        for spots in self._danger_buckets.values():
            for spot, radius in spots:
                detour = distance(start, spot) + distance(spot, goal)
                if detour <= direct + 2 * (radius + slack):
                    return False
        return True

    def column_safe(self, coord: Axial) -> bool:
        """Whether a column may step onto ``coord`` without meeting a garrison."""
        bq, br = coord.q // _BUCKET, coord.r // _BUCKET
        for dq in (-1, 0, 1):
            for dr in (-1, 0, 1):
                for spot, radius in self._danger_buckets.get((bq + dq, br + dr), ()):
                    if distance(spot, coord) <= radius:
                        return False
        return True
