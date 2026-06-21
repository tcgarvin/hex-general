"""Victory check (SPEC §15) — a pure query over end-of-turn state.

Unlike the execution phases, victory resolution changes nothing: it inspects a
`GameState` after a turn and reports whether the game is decided. Two scenario-
configurable conditions apply (SPEC §15, `VictoryConfig`):

  * **City elimination** (§15.1): a player who controls zero cities has lost. When
    only one player still holds a city, that player wins immediately.
  * **Turn limit** (§15.2): once the configured turn limit is reached, settlements
    are scored (town = 1, city = 3 by default) and the highest score wins.

City elimination is checked first: a player can be knocked out before the turn
limit. Ties — at the limit, or mutual elimination — are draws (no winner).

The result is a `VictoryStatus`. ``reason`` distinguishes an ongoing game from a
decided one; ``winner`` is the deciding player, or ``None`` when the game is still
ongoing *or* ended in a draw (``reason`` tells which).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.engine.models import GameState, PlayerId
from app.engine.stats import Terrain


class VictoryReason(StrEnum):
    """Why the game is (not) decided (SPEC §15)."""

    ONGOING = "ongoing"  # no victory condition met yet
    CITY_ELIMINATION = "city_elimination"  # decided by §15.1
    TURN_LIMIT = "turn_limit"  # decided by §15.2 scoring


class VictoryStatus(BaseModel):
    """The outcome of a victory check (SPEC §15).

    ``winner`` is set only on a decisive result; it is ``None`` when the game is
    ongoing (``reason == ONGOING``) or drawn (a decided ``reason`` with no single
    winner). ``eliminated`` lists players controlling zero cities while city
    elimination is in effect. ``scores`` is each player's settlement victory-point
    total (§15.2), always computed so a UI can show standings mid-game.
    """

    model_config = ConfigDict(extra="forbid")

    reason: VictoryReason
    winner: PlayerId | None
    eliminated: list[PlayerId]
    scores: dict[PlayerId, int]

    @property
    def decided(self) -> bool:
        """Whether the game is over (a winner or a draw was reached)."""
        return self.reason is not VictoryReason.ONGOING


def victory_status(state: GameState) -> VictoryStatus:
    """Evaluate the scenario's victory conditions against ``state`` (SPEC §15)."""
    config = state.config.victory
    terrain_at = {h.coord: h.terrain for h in state.map.hexes}

    cities: dict[PlayerId, int] = {p.id: 0 for p in state.players}
    scores: dict[PlayerId, int] = {p.id: 0 for p in state.players}
    for settlement in state.settlements:
        owner = settlement.controlled_by
        if owner is None:
            continue
        if terrain_at[settlement.coord] is Terrain.CITY:
            cities[owner] += 1
            scores[owner] += config.city_points
        else:
            scores[owner] += config.town_points

    eliminated = (
        [p.id for p in state.players if cities[p.id] == 0]
        if config.city_elimination
        else []
    )

    if config.city_elimination and eliminated:
        survivors = [p.id for p in state.players if cities[p.id] > 0]
        if len(survivors) <= 1:
            winner = survivors[0] if survivors else None
            return VictoryStatus(
                reason=VictoryReason.CITY_ELIMINATION,
                winner=winner,
                eliminated=eliminated,
                scores=scores,
            )

    if state.turn >= config.turn_limit:
        return VictoryStatus(
            reason=VictoryReason.TURN_LIMIT,
            winner=_top_scorer(scores),
            eliminated=eliminated,
            scores=scores,
        )

    return VictoryStatus(
        reason=VictoryReason.ONGOING,
        winner=None,
        eliminated=eliminated,
        scores=scores,
    )


def _top_scorer(scores: dict[PlayerId, int]) -> PlayerId | None:
    """The unique highest scorer, or ``None`` on a tie (a draw, SPEC §15.2)."""
    if not scores:
        return None
    best = max(scores.values())
    leaders = [player for player, score in scores.items() if score == best]
    return leaders[0] if len(leaders) == 1 else None
