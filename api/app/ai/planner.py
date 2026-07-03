"""SearchPolicy — Tier 1: coordinate ascent with engine simulation (AI_SPEC §4.7).

`SearchPolicy` starts from `ScriptedPolicy`'s plan (top candidate per unit) and
refines it with greedy coordinate ascent, scoring each trial by **simulating the
turn with the real engine** against the §4.4 opponent model (all visible enemies
Hold) and evaluating the result (§4.3). This is the §4.7 algorithm directly.

Performance note (§8). Phase 0 measured ``t_sim ≈ 148 ms`` per full turn, whose
raw value selects the §8 tier-3 branch (static-projection scoring, true sim only
on a final shortlist). That fallback was tried first and **failed the §7.3
SearchPolicy-vs-ScriptedPolicy gate**: a combat-blind static projection cannot see
which attacks actually pay off, so the refinement under-attacked and lost to the
aggressive scripted bot. Because the measured think time left ample headroom under
the §7.3 budget (mean ≪ 2 s at eight sims/turn), this policy instead runs the
spec's *primary* §4.7 true-simulation ascent, bounded by a per-turn ``sim_budget``
chosen to keep think time within budget — a richer, more faithful realisation of
§4.7 that the timing supports. Candidates are ordered by tactical preference
(`candidates.py`), so the budget is spent trying the most plausible swaps first.

The ascent starts from the scripted plan and accepts a swap only when the
simulation shows an improvement past a margin (see ``_ACCEPT_MARGIN_ACTIVE`` /
``_ACCEPT_MARGIN_PASSIVE`` for why passivity is held to a much higher bar), so
the returned plan's simulated score is never below scripted's own — and
self-jams, bad trades, and focus-fire all resolve for free because the real
engine scores them (AI_SPEC §2.1). Recruitments are fixed by rule (§4.6) before
refinement. Every plan returned is statically legal and deterministic given the
weights and the (redacted) view.
"""

from __future__ import annotations

from app.ai.board import BoardIndex
from app.ai.candidates import unit_candidates
from app.ai.evaluation import EvalWeights, evaluate
from app.ai.objectives import Objective, ObjectiveKind, assign_objectives
from app.ai.policy import BotPlan
from app.ai.recruit import plan_recruitment
from app.ai.simulate import resolve_plan
from app.engine.events import Event, TrafficJam
from app.engine.hex_coords import Axial, distance
from app.engine.models import (
    GameState,
    HoldOrder,
    Order,
    PlayerId,
    Recruitment,
    RestOrder,
)

# Under the §8 measurement (t_sim ~148ms) these keep SearchPolicy within the §7.3
# think-time gate (mean <= 2s, p95 <= 5s): at most ``_SIM_BUDGET`` true simulations
# per turn (~12 * ~0.16s ~= 1.9s worst case, less when the ascent converges early).
_MAX_SWEEPS = 3
_SIM_BUDGET = 12
# Alternatives considered per unit per sweep (the head of its preference-ordered
# candidate list). Trying every candidate (~7 alts) exhausts the budget on the
# first two units of a sweep; capping at the top few spreads the budget across
# ~4 units per sweep, which is where the coordination gains live.
_REFINE_ALTS = 4
# Penalty per own unit caught in a traffic jam in the simulated turn. A
# distance-conserving self-jam evaluates the same as a clean advance under §4.3
# (two units each stepping one hex ties one unit stepping two), so the plain score
# cannot always see it; this realises AI_SPEC §2.1's intent that a self-jamming
# plan "score poorly". Sized as a whole wasted unit-turn — large enough that
# un-jamming clears even the passive acceptance margin, since the un-jamming swap
# is often "one of the colliding units holds instead".
_JAM_PENALTY = 2.0
# Minimum simulated-score improvement for a swap to replace the tactical layer's
# top candidate, split by what the swap does. The §4.4 Hold opponent model prices
# standing still at zero risk and an advance/attack at its full immediate cost,
# so depth-1 simulation systematically overvalues passivity: arena-measured,
# accepting every ε>0 swap made SearchPolicy hold-lock its front line, get
# focus-fired down, and lose to its own (scripted) starting plan. The sim's real
# comparative advantage is *comparing actions* — which attack, which barrage
# target, capture vs trade, jam vs clean path — so a swap to another Move/Barrage
# needs only a small margin, while a swap to Hold/Rest (strategic passivity that
# depth-1 cannot price) must be backed by overwhelming evidence, e.g. an
# otherwise-certain unit loss.
_ACCEPT_MARGIN_ACTIVE = 0.25
_ACCEPT_MARGIN_PASSIVE = 3.0


def _own_jam_count(events: list[Event], own_ids: set[int]) -> int:
    """Number of our units caught in a traffic jam in a simulated turn (§2.1)."""
    return sum(
        len([uid for uid in event.unit_ids if uid in own_ids])
        for event in events
        if isinstance(event, TrafficJam)
    )


def _contact_first_order(board: BoardIndex) -> list[int]:
    """Own unit ids in contact-first stable order (§4.7 sweep order).

    Units nearest a visible enemy sweep first — engine simulation reveals the
    most for them (combat outcomes, ZOC stops, focus-fire kills), so the budget
    goes there before it is spent re-deriving obvious advances for units nowhere
    near the fight. Ties (including the no-visible-enemy case) break by unit id,
    keeping the order fully deterministic.
    """

    def key(uid: int) -> tuple[int, int]:
        unit = board.unit_by_id[uid]
        gaps = [distance(unit.coord, e.coord) for e in board.enemy_units]
        return (min(gaps) if gaps else 1_000, uid)

    return sorted((u.id for u in board.own_units), key=key)


class SearchPolicy:
    """Coordinate-ascent planner refined by engine simulation (AI_SPEC Tier 1)."""

    name = "search"

    def __init__(
        self,
        weights: EvalWeights | None = None,
        *,
        max_sweeps: int = _MAX_SWEEPS,
        sim_budget: int = _SIM_BUDGET,
    ) -> None:
        self._weights = weights if weights is not None else EvalWeights()
        self._max_sweeps = max_sweeps
        self._sim_budget = sim_budget

    def plan(self, view: GameState, slot: PlayerId) -> BotPlan:
        board = BoardIndex(view, slot)
        recruitments = plan_recruitment(board, slot)
        reserved: frozenset[Axial] = frozenset(r.coord for r in recruitments)
        assignment = assign_objectives(board, slot)

        # Candidates are generated once per turn and reused across sweeps (§8),
        # already ordered best-first by tactical preference (`candidates.py`).
        candidates: dict[int, list[Order]] = {}
        for unit in board.own_units:
            objective = assignment.get(
                unit.id, Objective(ObjectiveKind.SUPPORT, unit.coord, 0.0)
            )
            candidates[unit.id] = unit_candidates(board, unit, objective, reserved)

        if not board.own_units:
            return BotPlan(orders=[], recruitments=recruitments)

        own_ids = {u.id for u in board.own_units}
        enemy_hold: list[Order] = [HoldOrder(unit_id=e.id) for e in board.enemy_units]
        # Each unit's position anchor is its *assigned* objective, fixed from the
        # pre-turn view, so every simulated outcome is scored against the same
        # targets and the position gradient points where the strategic layer is
        # actually sending each unit (see `evaluate`).
        anchors = {uid: objective.coord for uid, objective in assignment.items()}

        current: dict[int, Order] = {uid: cands[0] for uid, cands in candidates.items()}
        best = self._sim_eval(
            view, slot, own_ids, current, enemy_hold, recruitments, anchors
        )
        sims = 1

        # Sweep in *contact-first* stable order (§4.7 allows any stable order):
        # simulation only reveals something the tactical scoring cannot already
        # see — jams, combat outcomes, ZOC stops — for units near the enemy, so
        # the budget is spent on those first instead of burning out in unit-id
        # order before reaching the units actually in contact. Units with no
        # contact keep their (deterministic) top candidate.
        sweep_order = _contact_first_order(board)

        for _sweep in range(self._max_sweeps):
            improved = False
            for uid in sweep_order:
                if sims >= self._sim_budget:
                    break
                for alt in candidates[uid][:_REFINE_ALTS]:
                    if alt == current[uid]:
                        continue
                    if sims >= self._sim_budget:
                        break
                    trial = dict(current)
                    trial[uid] = alt
                    score = self._sim_eval(
                        view, slot, own_ids, trial, enemy_hold, recruitments, anchors
                    )
                    sims += 1
                    margin = (
                        _ACCEPT_MARGIN_PASSIVE
                        if isinstance(alt, (HoldOrder, RestOrder))
                        else _ACCEPT_MARGIN_ACTIVE
                    )
                    if score > best + margin:
                        current, best, improved = trial, score, True
                        break  # take the first improving swap; move to the next unit
            if not improved or sims >= self._sim_budget:
                break

        return BotPlan(orders=list(current.values()), recruitments=recruitments)

    def _sim_eval(
        self,
        view: GameState,
        slot: PlayerId,
        own_ids: set[int],
        orders: dict[int, Order],
        enemy_hold: list[Order],
        recruitments: list[Recruitment],
        anchors: dict[int, Axial],
    ) -> float:
        """Simulate ``orders`` (+ enemy Hold) and score the result (AI_SPEC §4.7).

        The §4.4 opponent model has every visible enemy Hold; `resolve_plan` runs
        the real engine, so jams, ZOC stops, and simultaneous combat are exact. A
        self-jam is docked (`_JAM_PENALTY`) on top of the static evaluation.
        """
        full: list[Order] = list(orders.values()) + enemy_hold
        resolved, events = resolve_plan(view, full, recruitments)
        score = evaluate(resolved, slot, self._weights, anchors)
        return score - _JAM_PENALTY * _own_jam_count(events, own_ids)
