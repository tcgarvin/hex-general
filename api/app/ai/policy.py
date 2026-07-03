"""The `Policy` interface and its `BotPlan` output (AI_SPEC §4.1).

A `Policy` turns a fog-redacted view of the game into a full turn: unit orders
plus recruitments. The `view` a policy receives is always the redacted state for
its slot (`app.engine.fog.redact_state`), so a policy can never see hidden enemy
units — fog fairness is structural, not disciplinary. Every plan a policy returns
must be statically legal (`app.games.validation.validate_submission`); the arena
asserts this on every turn.

Policies are stateless across turns in v1: each `plan()` call reasons from the
current view only. Policies that use randomness take a seed at construction and
must be fully deterministic given ``(seed, view, slot)``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.engine.models import GameState, Order, PlayerId, Recruitment


class BotPlan(BaseModel):
    """One policy's complete submission for a turn: orders + recruitments.

    Mirrors the two halves of a human submission
    (`app.games.schemas.SubmitOrdersRequest`), so a plan can be validated by the
    same `validate_submission` path and stored as a submission verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    orders: list[Order] = Field(default_factory=list)
    recruitments: list[Recruitment] = Field(default_factory=list)


@runtime_checkable
class Policy(Protocol):
    """A turn planner for one player slot (AI_SPEC §4.1).

    ``name`` identifies the policy in the arena registry and result logs.
    ``plan`` receives the fog-redacted ``view`` for ``slot`` and returns a
    statically-legal `BotPlan`.
    """

    name: str

    def plan(self, view: GameState, slot: PlayerId) -> BotPlan: ...
