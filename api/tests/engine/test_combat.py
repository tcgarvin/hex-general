"""Tests for combat resolution (roadmap Phase 3; SPEC §12, §13).

Combat is exercised two ways: `resolve_contest`/`compute_damage` directly for the
formula and occupation rules, and end-to-end through `resolve_movement` for the
nasty simultaneous cases the roadmap names — dogpiles, lead-entrant selection,
three-way contests, edge swaps, and post-combat continuation. Prototype stats
(SPEC §5.3): infantry atk4/def5, armor atk7/def4, artillery atk5/def2; damage is
clamped to 1..5 (SPEC §13.1).
"""

from __future__ import annotations

from app.engine.combat import compute_damage, lead_entrant, resolve_contest
from app.engine.events import (
    CombatParticipant,
    CombatResolved,
    CombatRole,
    Event,
    MoveBlocked,
    MoveBlockedReason,
    UnitMoved,
)
from app.engine.models import Order, Unit
from app.engine.movement import resolve_movement
from app.engine.stats import UnitType
from engine.helpers import alive, at, ax, build, hold, hp_of, move


def _combats(events: list[Event]) -> list[CombatResolved]:
    return [e for e in events if isinstance(e, CombatResolved)]


def _participant(combat: CombatResolved, unit_id: int) -> CombatParticipant:
    return next(p for p in combat.participants if p.unit_id == unit_id)


# --- Formula & helpers (SPEC §13.1, §12.6) ----------------------------------


def test_compute_damage_clamps_to_one_through_five() -> None:
    # Infantry vs armor: 4 - 4 = 0, floored to 1.
    assert compute_damage(UnitType.INFANTRY, UnitType.ARMOR, 0) == 1
    # Heavy terrain drives raw negative — still floored to 1.
    assert compute_damage(UnitType.ARMOR, UnitType.INFANTRY, 5) == 1
    # Armor vs artillery: 7 - 2 = 5, the ceiling.
    assert compute_damage(UnitType.ARMOR, UnitType.ARTILLERY, 0) == 5


def test_lead_entrant_tiebreak_type_then_hp_then_id() -> None:
    armor = Unit(id=9, type=UnitType.ARMOR, owner="blue", coord=ax(0, 0), hp=3)
    inf_strong = Unit(id=2, type=UnitType.INFANTRY, owner="blue", coord=ax(0, 0), hp=10)
    inf_weak = Unit(id=1, type=UnitType.INFANTRY, owner="blue", coord=ax(0, 0), hp=4)
    # Armor outranks both infantry despite lower HP / higher id.
    assert lead_entrant([inf_strong, armor, inf_weak]) == 9
    # Among the infantry, higher HP wins over lower id.
    assert lead_entrant([inf_weak, inf_strong]) == 2


def test_terrain_bonus_only_protects_the_stationary_defender() -> None:
    # A woods defender takes less than it would on plains; the moving attacker
    # never benefits from terrain (SPEC §12.10).
    state = build(
        ["wh."],  # (0,0) woods, (1,0) hills, (2,0) plains
        [
            {"type": "armor", "owner": "blue", "q": 2, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 10},
        ],
    )
    units = {u.id: u for u in state.units}
    hexes = {h.coord: h for h in state.map.hexes}
    outcome = resolve_contest(units[2].coord, units[2], [units[1]], set(), hexes)
    # Hills give +1: armor deals clamp(7 - 5 - 1) = 1 to the defender.
    assert outcome.results[2].damage_taken == 1
    assert outcome.results[2].terrain_bonus == 1
    assert outcome.results[1].terrain_bonus == 0  # attacker gets nothing


# --- 1v1 into-enemy attacks -------------------------------------------------


def test_both_survive_attacker_does_not_enter() -> None:
    # The §16.3 shape: armor attacks full-health infantry on plains; neither dies,
    # so nobody enters and the attacker stays put.
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0))])

    assert at(new_state, 0, 0) == 1  # attacker stayed
    assert hp_of(new_state, 1) == 9  # took 1 from the defender
    assert hp_of(new_state, 2) == 8  # took 2 from the attacker
    (combat,) = _combats(events)
    assert combat.entrant_id is None
    assert _participant(combat, 2).role is CombatRole.DEFENDER
    assert _participant(combat, 1).role is CombatRole.ATTACKER


def test_attacker_destroys_defender_and_enters() -> None:
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 2},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0))])

    assert at(new_state, 1, 0) == 1  # armor occupied the cleared hex
    assert not alive(new_state, 2)
    assert hp_of(new_state, 1) == 9
    (combat,) = _combats(events)
    assert combat.entrant_id == 1
    assert combat.entrant_continued is False  # path was a single step


def test_hold_bonus_reduces_damage_to_defender() -> None:
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},
        ],
    )
    orders: list[Order] = [move(1, (1, 0)), hold(2)]
    new_state, events = resolve_movement(state, orders)

    assert hp_of(new_state, 2) == 9  # clamp(7 - 5 - 1) = 1 instead of 2
    (combat,) = _combats(events)
    assert _participant(combat, 2).hold_bonus == 1


def test_mutual_destruction_leaves_hex_empty() -> None:
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0, "hp": 1},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 2},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0))])

    assert not alive(new_state, 1)
    assert not alive(new_state, 2)
    assert at(new_state, 1, 0) is None
    (combat,) = _combats(events)
    assert combat.entrant_id is None


# --- Multi-unit attacks (SPEC §12.4) ----------------------------------------


def test_dogpile_defender_retaliates_against_every_attacker() -> None:
    # Two armor (each dealing 2) destroy a 3-HP infantry; the lone defender deals
    # its full damage back to *both* attackers (SPEC §12.4), and the lead entrant
    # (lower id, armor) occupies the hex while the other stays.
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "armor", "owner": "blue", "q": 2, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 3},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0)), move(2, (1, 0))])

    assert not alive(new_state, 3)
    assert at(new_state, 1, 0) == 1  # lead entrant
    assert at(new_state, 2, 0) == 2  # other attacker stayed
    assert hp_of(new_state, 1) == 9  # both took the defender's full retaliation
    assert hp_of(new_state, 2) == 9
    (combat,) = _combats(events)
    assert combat.entrant_id == 1


def test_lead_entrant_prefers_armor_over_infantry() -> None:
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 3},
        ],
    )
    new_state, _ = resolve_movement(state, [move(1, (1, 0)), move(2, (1, 0))])

    assert at(new_state, 1, 0) == 1  # armor took the hex
    assert at(new_state, 2, 0) == 2  # infantry stayed


def test_destroyed_lead_entrant_has_no_fallback() -> None:
    # The lead (armor) and the defender (red armor) trade fatal blows; the
    # surviving non-lead infantry does NOT enter (SPEC §12.5).
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0, "hp": 1},
            {"type": "infantry", "owner": "blue", "q": 2, "r": 0},
            {"type": "armor", "owner": "red", "q": 1, "r": 0, "hp": 4},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0)), move(2, (1, 0))])

    assert not alive(new_state, 1)  # lead armor destroyed
    assert not alive(new_state, 3)  # red armor destroyed
    assert at(new_state, 1, 0) is None  # nobody entered
    assert at(new_state, 2, 0) == 2  # infantry stayed
    assert hp_of(new_state, 2) == 8  # took the red armor's 2 retaliation
    (combat,) = _combats(events)
    assert combat.entrant_id is None


# --- Multi-side contest (SPEC §12.8) ----------------------------------------


def test_three_way_contest_with_survivors_admits_no_one() -> None:
    # Three players converge on an empty hex from pairwise non-adjacent starts
    # (so none is ZOC-locked); all survive, so no side may occupy it.
    state = build(
        ["...", "...", "..."],
        [
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
            {"type": "infantry", "owner": "red", "q": 2, "r": 1},
            {"type": "infantry", "owner": "green", "q": 0, "r": 2},
        ],
    )
    orders: list[Order] = [move(1, (1, 1)), move(2, (1, 1)), move(3, (1, 1))]
    new_state, events = resolve_movement(state, orders)

    assert at(new_state, 1, 1) is None  # contested hex stays empty
    assert at(new_state, 1, 0) == 1  # everyone held their start
    assert at(new_state, 2, 1) == 2
    assert at(new_state, 0, 2) == 3
    for uid in (1, 2, 3):
        assert hp_of(new_state, uid) == 8  # 1 damage from each of two opponents
    (combat,) = _combats(events)
    assert combat.entrant_id is None
    assert len(combat.participants) == 3


# --- Artillery may not attack by moving (SPEC §5.4) -------------------------


def test_artillery_cannot_attack_by_moving() -> None:
    state = build(
        ["..."],
        [
            {"type": "artillery", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0))])

    assert at(new_state, 0, 0) == 1  # stayed
    assert alive(new_state, 2) and hp_of(new_state, 2) == 10  # no combat
    assert not _combats(events)
    assert events == [
        MoveBlocked(
            pulse=1,
            unit_id=1,
            origin=ax(0, 0),
            target=ax(1, 0),
            reason=MoveBlockedReason.ATTACK_FORBIDDEN,
        )
    ]


# --- Edge swaps (SPEC §12.9) ------------------------------------------------


def test_edge_swap_both_survive_stay_put() -> None:
    state = build(
        [".."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "armor", "owner": "red", "q": 1, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0)), move(2, (0, 0))])

    assert at(new_state, 0, 0) == 1  # both remained in their original hexes
    assert at(new_state, 1, 0) == 2
    assert hp_of(new_state, 1) == 7  # clamp(7 - 4) = 3 each
    assert hp_of(new_state, 2) == 7
    (combat,) = _combats(events)
    assert combat.entrant_id is None


def test_edge_swap_survivor_completes_the_move() -> None:
    state = build(
        [".."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 1},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0)), move(2, (0, 0))])

    assert at(new_state, 1, 0) == 1  # armor completed the swap into red's hex
    assert not alive(new_state, 2)
    assert hp_of(new_state, 1) == 9
    (combat,) = _combats(events)
    assert combat.entrant_id == 1


def test_edge_swap_both_destroyed_empties_both_hexes() -> None:
    state = build(
        [".."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0, "hp": 1},
            {"type": "armor", "owner": "red", "q": 1, "r": 0, "hp": 3},
        ],
    )
    new_state, _ = resolve_movement(state, [move(1, (1, 0)), move(2, (0, 0))])

    assert at(new_state, 0, 0) is None
    assert at(new_state, 1, 0) is None
    assert not alive(new_state, 1)
    assert not alive(new_state, 2)


# --- Continuation / breakthrough (SPEC §11.2, §12.5) ------------------------


def test_armor_breaks_through_and_continues() -> None:
    # Armor (move 4) destroys a weak defender, occupies its hex, and — with pulses
    # left and no ZOC after the kill — drives on to the next hex the same turn.
    state = build(
        ["...."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": 1},
        ],
    )
    new_state, events = resolve_movement(state, [move(1, (1, 0), (2, 0))])

    assert at(new_state, 2, 0) == 1  # broke through to the far hex
    assert not alive(new_state, 2)
    combat = _combats(events)[0]
    assert combat.pulse == 1
    assert combat.entrant_id == 1
    assert combat.entrant_continued is True
    follow_on = [e for e in events if isinstance(e, UnitMoved)]
    assert follow_on and follow_on[-1].pulse == 2
