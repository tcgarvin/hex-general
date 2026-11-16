"""Assault and engage attacks (WORLD_SPEC §4.8).

A Move order's ``attack`` kind changes only occupation: an engaging unit deals
and takes exactly the damage an assault would, but never enters its final path
hex, is never the entrant of any contest, and stops after any fight. Prototype
stats (SPEC §5.3): infantry atk4/def5 move 2, armor atk7/def4 move 4; damage is
clamped to 1..5 and units start at 10 HP.
"""

from __future__ import annotations

from app.engine.combat import resolve_contest
from app.engine.events import (
    CombatParticipant,
    CombatResolved,
    Event,
    MoveBlocked,
    MoveBlockedReason,
    UnitMoved,
)
from app.engine.models import AttackKind, GameState, MoveOrder, Order, Posture
from app.engine.movement import resolve_movement
from engine.helpers import alive, at, ax, build, engage, hp_of, move


def _combats(events: list[Event]) -> list[CombatResolved]:
    return [e for e in events if isinstance(e, CombatResolved)]


def _blocks(events: list[Event]) -> list[MoveBlocked]:
    return [e for e in events if isinstance(e, MoveBlocked)]


def _participant(combat: CombatResolved, unit_id: int) -> CombatParticipant:
    return next(p for p in combat.participants if p.unit_id == unit_id)


def _duel(defender_hp: int) -> GameState:
    """Blue armor (id 1) at (0,0) beside red infantry (id 2) at (1,0)."""
    return build(
        ["...."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": defender_hp},
        ],
    )


# --- The order field ----------------------------------------------------------


def test_a_move_order_assaults_unless_told_to_engage() -> None:
    parsed = MoveOrder.model_validate(
        {"kind": "move", "unit_id": 1, "path": [{"q": 1, "r": 0}]}
    )
    assert parsed.attack is AttackKind.ASSAULT
    assert engage(1, (1, 0)).attack is AttackKind.ENGAGE


# --- Assault is unchanged -----------------------------------------------------


def test_an_assault_takes_the_hex_it_clears_and_moves_on() -> None:
    state = _duel(defender_hp=2)
    new_state, events = resolve_movement(state, [move(1, (1, 0), (2, 0))])

    assert at(new_state, 2, 0) == 1  # entered, then kept going
    (combat,) = _combats(events)
    assert combat.entrant_id == 1 and combat.entrant_continued
    assert not _participant(combat, 1).engaging


# --- Engage against a held hex --------------------------------------------------


def test_an_engager_clears_the_hex_but_stays_beside_it() -> None:
    state = _duel(defender_hp=2)
    new_state, events = resolve_movement(state, [engage(1, (1, 0))])

    assert not alive(new_state, 2)
    assert at(new_state, 0, 0) == 1  # never entered
    assert at(new_state, 1, 0) is None
    (combat,) = _combats(events)
    assert combat.entrant_id is None
    assert _participant(combat, 1).engaging
    assert not _participant(combat, 2).engaging
    assert not any(isinstance(e, UnitMoved) for e in events)


def test_engage_deals_and_takes_the_same_damage_as_an_assault() -> None:
    assault_state, assault_events = resolve_movement(_duel(10), [move(1, (1, 0))])
    engage_state, engage_events = resolve_movement(_duel(10), [engage(1, (1, 0))])

    for unit_id in (1, 2):
        assert hp_of(assault_state, unit_id) == hp_of(engage_state, unit_id)
    (assault,) = _combats(assault_events)
    (engaged,) = _combats(engage_events)
    for unit_id in (1, 2):
        a, e = _participant(assault, unit_id), _participant(engaged, unit_id)
        assert (a.damage_dealt, a.damage_taken) == (e.damage_dealt, e.damage_taken)


# --- Engage when the final hex holds no enemy -----------------------------------


def _fleeing_target() -> GameState:
    """Blue armor (id 1) at (0,0); red infantry (id 2) at (3,0), about to leave."""
    return build(
        ["......"],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0},
        ],
    )


def test_an_engager_stops_beside_a_final_hex_the_enemy_has_left() -> None:
    orders: list[Order] = [engage(1, (1, 0), (2, 0), (3, 0)), move(2, (4, 0), (5, 0))]
    new_state, events = resolve_movement(_fleeing_target(), orders)

    assert at(new_state, 2, 0) == 1  # stopped one short of its final hex
    assert at(new_state, 5, 0) == 2
    assert _combats(events) == []
    (block,) = _blocks(events)
    assert block.unit_id == 1
    assert block.reason is MoveBlockedReason.ENGAGE_HALT
    assert block.origin == ax(2, 0) and block.target == ax(3, 0)


def test_an_assault_walks_into_a_final_hex_the_enemy_has_left() -> None:
    orders: list[Order] = [move(1, (1, 0), (2, 0), (3, 0)), move(2, (4, 0), (5, 0))]
    new_state, _ = resolve_movement(_fleeing_target(), orders)

    assert at(new_state, 3, 0) == 1


def test_an_engager_stops_beside_a_friendly_final_hex() -> None:
    state = build(
        ["..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 1, "r": 0},
        ],
    )
    new_state, events = resolve_movement(state, [engage(1, (1, 0))])

    assert at(new_state, 0, 0) == 1
    (block,) = _blocks(events)
    assert block.reason is MoveBlockedReason.ENGAGE_HALT


def test_an_engager_halting_short_does_not_jam_a_friend() -> None:
    # Two blue units aim at the same empty hex; the engager halts, so the other
    # walks in instead of both jamming.
    state = build(
        ["...", "..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 0, "r": 1},
        ],
    )
    new_state, events = resolve_movement(state, [engage(1, (1, 0)), move(2, (1, 0))])

    assert at(new_state, 0, 0) == 1
    assert at(new_state, 1, 0) == 2
    (block,) = _blocks(events)
    assert block.unit_id == 1 and block.reason is MoveBlockedReason.ENGAGE_HALT


def test_an_engager_fights_an_enemy_racing_into_its_final_hex() -> None:
    # The final hex is empty at the start of the pulse, but red steps into it in
    # the same pulse: the engager contests it as an assault would, and wins
    # without entering.
    state = build(
        ["...", "..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 1, "r": 1, "hp": 2},
        ],
    )
    new_state, events = resolve_movement(state, [engage(1, (1, 0)), move(2, (1, 0))])

    (combat,) = _combats(events)
    assert combat.coord == ax(1, 0)
    assert not alive(new_state, 2)
    assert combat.entrant_id is None
    assert at(new_state, 0, 0) == 1
    assert at(new_state, 1, 0) is None


# --- Contests on the way --------------------------------------------------------


def _interceptor() -> GameState:
    """Blue armor (id 1) at (0,0) heading east; weak red infantry (id 2) at (2,1)
    steps onto the blue path at (2,0) in pulse 1."""
    return build(
        [".....", "....."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 2, "r": 1, "hp": 2},
        ],
    )


def test_an_engager_never_occupies_after_winning_a_fight_on_the_way() -> None:
    orders: list[Order] = [engage(1, (1, 0), (2, 0), (3, 0), (4, 0)), move(2, (2, 0))]
    new_state, events = resolve_movement(_interceptor(), orders)

    (combat,) = _combats(events)
    assert combat.coord == ax(2, 0)
    assert not alive(new_state, 2)
    assert combat.entrant_id is None
    assert at(new_state, 1, 0) == 1  # stopped after the fight, no pursuit
    assert at(new_state, 2, 0) is None


def test_an_assault_breaks_through_the_same_interception() -> None:
    orders: list[Order] = [move(1, (1, 0), (2, 0), (3, 0), (4, 0)), move(2, (2, 0))]
    new_state, events = resolve_movement(_interceptor(), orders)

    (combat,) = _combats(events)
    assert combat.entrant_id == 1 and combat.entrant_continued
    assert at(new_state, 4, 0) == 1


# --- Several attackers on one hex -----------------------------------------------


def _dogpile(defender_hp: int, infantry_hp: int = 10) -> GameState:
    """Blue armor (id 1) and blue infantry (id 2) both beside red infantry (id 3)
    at (1,0)."""
    return build(
        ["...", "..."],
        [
            {"type": "armor", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 0, "r": 1, "hp": infantry_hp},
            {"type": "infantry", "owner": "red", "q": 1, "r": 0, "hp": defender_hp},
        ],
    )


def test_all_assaulting_the_armor_leads_in() -> None:
    new_state, events = resolve_movement(
        _dogpile(defender_hp=3), [move(1, (1, 0)), move(2, (1, 0))]
    )
    (combat,) = _combats(events)
    assert combat.entrant_id == 1
    assert at(new_state, 1, 0) == 1


def test_only_the_assaulting_unit_takes_the_hex_beside_an_engager() -> None:
    # Armor would lead an all-assault dogpile; engaging, it leaves the hex to the
    # infantry, the one assaulter.
    new_state, events = resolve_movement(
        _dogpile(defender_hp=3), [engage(1, (1, 0)), move(2, (1, 0))]
    )
    (combat,) = _combats(events)
    assert not alive(new_state, 3)
    assert combat.entrant_id == 2
    assert at(new_state, 1, 0) == 2
    assert at(new_state, 0, 0) == 1
    assert _participant(combat, 1).engaging
    assert not _participant(combat, 2).engaging


def test_nobody_enters_when_every_winner_engaged() -> None:
    new_state, events = resolve_movement(
        _dogpile(defender_hp=3), [engage(1, (1, 0)), engage(2, (1, 0))]
    )
    (combat,) = _combats(events)
    assert not alive(new_state, 3)
    assert combat.entrant_id is None
    assert at(new_state, 1, 0) is None
    assert at(new_state, 0, 0) == 1 and at(new_state, 0, 1) == 2


def test_a_dead_assaulting_lead_lets_no_engager_in() -> None:
    # The lone assaulter (1 HP infantry) dies to the defender's retaliation; the
    # surviving engager does not take its place.
    new_state, events = resolve_movement(
        _dogpile(defender_hp=3, infantry_hp=1), [engage(1, (1, 0)), move(2, (1, 0))]
    )
    (combat,) = _combats(events)
    assert not alive(new_state, 2) and not alive(new_state, 3)
    assert combat.entrant_id is None
    assert at(new_state, 1, 0) is None
    assert at(new_state, 0, 0) == 1


def test_resolve_contest_picks_the_lead_among_assaulters_only() -> None:
    state = _dogpile(defender_hp=3)
    units = {u.id: u for u in state.units}
    target = ax(1, 0)
    attackers = [units[1], units[2]]

    assault = resolve_contest(target, units[3], attackers, set(), 0)
    mixed = resolve_contest(target, units[3], attackers, set(), 0, frozenset({1}))
    engaged = resolve_contest(target, units[3], attackers, set(), 0, frozenset({1, 2}))

    assert (assault.entrant_id, mixed.entrant_id, engaged.entrant_id) == (1, 2, None)
    assert assault.results == mixed.results == engaged.results


# --- Edge swaps -------------------------------------------------------------------


def test_a_surviving_engager_does_not_cross_an_edge_swap() -> None:
    new_state, events = resolve_movement(
        _duel(defender_hp=1), [engage(1, (1, 0), (2, 0)), move(2, (0, 0))]
    )
    (combat,) = _combats(events)
    assert not alive(new_state, 2)
    assert combat.entrant_id is None
    assert at(new_state, 0, 0) == 1
    assert _participant(combat, 1).engaging


def test_an_assaulter_still_crosses_an_edge_swap_against_an_engager() -> None:
    # Red engages back into blue's hex; blue assaults and wins, so blue crosses.
    new_state, events = resolve_movement(
        _duel(defender_hp=1), [move(1, (1, 0)), engage(2, (0, 0))]
    )
    (combat,) = _combats(events)
    assert combat.entrant_id == 1
    assert at(new_state, 1, 0) == 1
    assert _participant(combat, 2).engaging


# --- Columns (WORLD_SPEC §4.6) -----------------------------------------------------


def test_an_engager_never_enters_a_hex_with_stuck_hostile_columns() -> None:
    # A one-hex corridor: red infantry (id 2) at (3,0) shares its hex with a red
    # column (id 5) that has no room to deploy (red holds (4,0) and (5,0); (1,0)
    # is in blue ZOC). Blue kills the infantry either way and stays beside the
    # column: an assault because hostile columns bar entry, an engage by rule.
    # The column then deploys on the hex it kept.
    state = build(
        ["......", "~~~~~~"],
        [
            {"type": "armor", "owner": "blue", "q": 2, "r": 0},
            {"type": "infantry", "owner": "red", "q": 3, "r": 0, "hp": 2},
            {"type": "infantry", "owner": "red", "q": 4, "r": 0},
            {"type": "infantry", "owner": "red", "q": 5, "r": 0},
            {"type": "infantry", "owner": "red", "q": 0, "r": 0},
        ],
    )
    for unit in state.units:
        if unit.id == 5:  # the column, packed onto the infantry's hex
            unit.posture = Posture.COLUMN
            unit.coord = ax(3, 0)

    for order in (engage(1, (3, 0)), move(1, (3, 0))):
        new_state, events = resolve_movement(state, [order])
        (combat,) = _combats(events)
        assert not alive(new_state, 2)
        assert combat.entrant_id is None
        assert at(new_state, 2, 0) == 1
        assert at(new_state, 3, 0) == 5  # the column deploys there next pulse
        assert _participant(combat, 1).engaging is (order.attack is AttackKind.ENGAGE)
