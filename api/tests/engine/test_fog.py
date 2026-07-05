"""Tests for fog-of-war redaction of state and events (SPEC §9, roadmap P6)."""

from app.engine.events import (
    BarrageHit,
    BarrageResolved,
    CombatParticipant,
    CombatResolved,
    CombatRole,
    EconomyResolved,
    Event,
    OrderMooted,
    OrderMootedReason,
    RecruitmentVoided,
    RecruitmentVoidedReason,
    UnitMoved,
)
from app.engine.fog import filter_events, redact_state
from app.engine.hex_coords import Axial
from app.engine.models import GameState
from app.engine.stats import UnitType
from engine.helpers import build


def _combatant(unit_id: int, owner: str, role: CombatRole) -> CombatParticipant:
    return CombatParticipant(
        unit_id=unit_id,
        unit_type=UnitType.INFANTRY,
        owner=owner,
        role=role,
        terrain_bonus=0,
        hold_bonus=0,
        damage_dealt=1,
        damage_taken=1,
        hp_after=9,
        destroyed=False,
    )


def _two_player_strip() -> GameState:
    # Blue at the left end sees (0,0)..(2,0); the red unit at the right end is
    # nine hexes away, deep in the fog.
    return build(
        [".........."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "red", "q": 9, "r": 0},  # id 2
        ],
    )


def test_redact_hides_enemy_units_outside_vision() -> None:
    state = build(
        [".........."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},  # id 2, adjacent
            {"type": "infantry", "owner": "red", "q": 9, "r": 0},  # id 3, far
        ],
    )
    redacted = redact_state(state, "blue")
    visible_ids = {u.id for u in redacted.units}
    assert visible_ids == {1, 2}  # own unit + the adjacent enemy; the far one hidden


def test_redact_preserves_visible_enemy_facing() -> None:
    # A visible enemy's cosmetic facing is public: it survives redaction intact
    # (roadmap U0 — the client renders visible enemies too).
    state = build(
        ["..."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},  # id 2, adjacent
        ],
    )
    red = next(u for u in state.units if u.id == 2)
    assert red.facing == "left"  # red loads facing the blue unit on its left
    redacted = redact_state(state, "blue")
    red_view = next(u for u in redacted.units if u.id == 2)
    assert red_view.facing == red.facing


def test_redact_keeps_all_of_the_viewers_own_units() -> None:
    state = build(
        [".........."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "blue", "q": 9, "r": 0},  # own, far away
        ],
    )
    redacted = redact_state(state, "blue")
    assert {u.id for u in redacted.units} == {1, 2}


def test_redact_does_not_mutate_the_input_state() -> None:
    state = _two_player_strip()
    redact_state(state, "blue")
    assert len(state.units) == 2  # original is untouched


def test_redact_is_symmetric_per_player() -> None:
    state = _two_player_strip()
    assert {u.id for u in redact_state(state, "blue").units} == {1}
    assert {u.id for u in redact_state(state, "red").units} == {2}


def test_filter_drops_enemy_events_in_the_fog() -> None:
    state = _two_player_strip()
    events: list[Event] = [
        UnitMoved(pulse=1, unit_id=2, origin=Axial(q=8, r=0), dest=Axial(q=9, r=0))
    ]
    assert filter_events(events, state, "blue") == []


def test_filter_keeps_enemy_events_on_visible_hexes() -> None:
    state = _two_player_strip()
    # Red steps onto (2,0), which blue can see — blue witnesses the move.
    events: list[Event] = [
        UnitMoved(pulse=1, unit_id=2, origin=Axial(q=3, r=0), dest=Axial(q=2, r=0))
    ]
    assert filter_events(events, state, "blue") == events


def test_filter_keeps_own_unit_events_even_in_the_fog() -> None:
    state = _two_player_strip()
    # A combat far away on (9,0) that blue's unit took part in is always witnessed.
    combat = CombatResolved(
        pulse=1,
        coord=Axial(q=9, r=0),
        participants=[
            _combatant(1, "blue", CombatRole.ATTACKER),
            _combatant(2, "red", CombatRole.DEFENDER),
        ],
    )
    assert filter_events([combat], state, "blue") == [combat]


def test_filter_drops_enemy_only_combat_in_the_fog() -> None:
    state = _two_player_strip()
    combat = CombatResolved(
        pulse=1,
        coord=Axial(q=9, r=0),
        participants=[_combatant(2, "red", CombatRole.DEFENDER)],
    )
    assert filter_events([combat], state, "blue") == []


def test_filter_keeps_barrage_that_hits_your_unit() -> None:
    state = _two_player_strip()
    # Enemy artillery (unknown to blue) shells blue's unit far from blue's sight.
    barrage = BarrageResolved(
        artillery_id=2,
        target=Axial(q=9, r=0),
        hit=BarrageHit(
            unit_id=1,
            unit_type=UnitType.INFANTRY,
            owner="blue",
            terrain_bonus=0,
            hold_bonus=0,
            damage=3,
            hp_after=7,
            destroyed=False,
        ),
    )
    assert filter_events([barrage], state, "blue") == [barrage]


def test_filter_makes_economy_private_to_its_owner() -> None:
    state = _two_player_strip()
    blue_econ = EconomyResolved(
        kind="economy_resolved",
        player="blue",
        income=2,
        upkeep=1,
        net=1,
        bank_before=0,
        bank_after=1,
        lost_to_cap=0,
    )
    red_econ = EconomyResolved(
        kind="economy_resolved",
        player="red",
        income=3,
        upkeep=2,
        net=1,
        bank_before=0,
        bank_after=1,
        lost_to_cap=0,
    )
    assert filter_events([blue_econ, red_econ], state, "blue") == [blue_econ]


def test_filter_makes_order_mooted_private_to_the_owner() -> None:
    # Blue and red stand adjacent, so red sees every hex involved — but a mooted
    # order is private bookkeeping (the event carries no location), so red never
    # learns that blue's dead unit had an order at all.
    state = build(
        ["..."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},  # id 2
        ],
    )
    mooted = OrderMooted(
        unit_id=7, owner="blue", reason=OrderMootedReason.DESTROYED_IN_BARRAGE
    )
    assert filter_events([mooted], state, "blue") == [mooted]
    assert filter_events([mooted], state, "red") == []


def test_filter_makes_recruitment_voided_private_to_the_owner() -> None:
    # Red stands adjacent to blue's city, so red can see the hex the voided
    # recruit named — but the event is still private to blue: a voided recruit
    # changes nothing on the board, and gating on the city hex would leak blue's
    # recruit *intent* to anyone who can merely see the city.
    state = build(
        ["c.."],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},  # id 1
            {"type": "infantry", "owner": "red", "q": 1, "r": 0},  # id 2
        ],
        settlements=[{"q": 0, "r": 0, "owner": "blue"}],
    )
    voided = RecruitmentVoided(
        owner="blue",
        coord=Axial(q=0, r=0),
        unit_type=UnitType.INFANTRY,
        reason=RecruitmentVoidedReason.HEX_OCCUPIED,
    )
    assert filter_events([voided], state, "blue") == [voided]
    assert filter_events([voided], state, "red") == []


def test_filter_preserves_event_order() -> None:
    state = _two_player_strip()
    near = UnitMoved(pulse=1, unit_id=1, origin=Axial(q=0, r=0), dest=Axial(q=1, r=0))
    far = UnitMoved(pulse=1, unit_id=2, origin=Axial(q=9, r=0), dest=Axial(q=8, r=0))
    blue_econ = EconomyResolved(
        kind="economy_resolved",
        player="blue",
        income=2,
        upkeep=1,
        net=1,
        bank_before=0,
        bank_after=1,
        lost_to_cap=0,
    )
    result = filter_events([near, far, blue_econ], state, "blue")
    assert result == [near, blue_econ]
