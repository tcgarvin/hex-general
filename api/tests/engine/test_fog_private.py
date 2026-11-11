"""What one world player may learn of another's turn (WORLD_SPEC §7, SPEC §9).

Blue is the viewer; red is the other player. Each test feeds `filter_events` one
kind of event about red and checks what blue receives: nothing, the event without
its private fields, or the whole event. A last group checks `redact_state`.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.engine.economy_rules import Building, Stocks, WorldEconomy
from app.engine.events import (
    BarrageHit,
    BarrageResolved,
    ColumnHeld,
    ColumnStuck,
    CombatParticipant,
    CombatResolved,
    CombatRole,
    ConstructionCompleted,
    ConstructionStarted,
    ConstructionVoided,
    ConstructionVoidedReason,
    DeployCause,
    EconomyResolved,
    Event,
    MoveBlocked,
    MoveBlockedReason,
    OrderMooted,
    OrderMootedReason,
    RecruitmentVoided,
    RecruitmentVoidedReason,
    SettlementCaptured,
    StocksResolved,
    TrafficJam,
    TravelEnded,
    TravelEndReason,
    UnitDeployed,
    UnitDisbanded,
    UnitMoved,
    UnitPacked,
    UnitRepaired,
    WorldUnitRecruited,
)
from app.engine.fog import (
    BarrageResolvedView,
    UnitDisbandedView,
    WorldUnitRecruitedView,
    filter_events,
    redact_state,
)
from app.engine.hex_coords import Axial
from app.engine.models import (
    Construction,
    GameState,
    Player,
    Posture,
    StandingTravel,
)
from app.engine.stats import Terrain, UnitType
from engine.helpers import build

INF, ART = UnitType.INFANTRY, UnitType.ARTILLERY

# A 12-hex strip. Blue's infantry at (0,0) sees (0..3, 0). Red has an infantry
# in sight at (2,0) (id 2), one deep in the fog at (9,0) (id 3) and a gun in the
# fog at (8,0) (id 4).
SEEN = Axial(q=2, r=0)
EDGE = Axial(q=3, r=0)  # the last hex blue sees
DARK = Axial(q=4, r=0)  # the first hex blue does not see
FAR = Axial(q=9, r=0)
BLUE_UNIT, RED_SEEN, RED_FAR, RED_GUN = 1, 2, 3, 4
RED_STOCKS = Stocks(manpower=27, fuel=30, steel=10)


def _world() -> GameState:
    state = build(
        ["............"],
        [
            {"type": "infantry", "owner": "blue", "q": 0, "r": 0},
            {"type": "infantry", "owner": "red", "q": 2, "r": 0},
            {"type": "infantry", "owner": "red", "q": 9, "r": 0},
            {"type": "artillery", "owner": "red", "q": 8, "r": 0},
        ],
    )
    return state.model_copy(
        update={
            "config": state.config.model_copy(update={"economy": WorldEconomy()}),
            "players": [
                Player(id="blue", stocks=Stocks(manpower=5)),
                Player(
                    id="red",
                    stocks=RED_STOCKS,
                    buildings={Building.BARRACKS: 2},
                    construction=Construction(
                        building=Building.DEPOT, level=1, completes_on=9
                    ),
                ),
            ],
            "standing_orders": [
                StandingTravel(
                    unit_id=RED_FAR, destination=Axial(q=11, r=0), route=[FAR]
                )
            ],
        }
    )


def _seen_by_blue(event: Event) -> list[dict[str, Any]]:
    return [_dump(e) for e in filter_events([event], _world(), "blue")]


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _hit(unit_id: int, owner: str, unit_type: UnitType = INF) -> BarrageHit:
    return BarrageHit(
        unit_id=unit_id,
        unit_type=unit_type,
        owner=owner,
        terrain_bonus=0,
        hold_bonus=0,
        damage=3,
        hp_after=7,
        destroyed=False,
    )


def _fighter(unit_id: int, owner: str, role: CombatRole) -> CombatParticipant:
    return CombatParticipant(
        unit_id=unit_id,
        unit_type=INF,
        owner=owner,
        role=role,
        terrain_bonus=0,
        hold_bonus=0,
        damage_dealt=1,
        damage_taken=1,
        hp_after=9,
        destroyed=False,
    )


# --- Private bookkeeping: never shown to anyone but its owner -------------------

PRIVATE_TO_RED: list[Event] = [
    StocksResolved(
        player="red",
        income=Stocks(manpower=5),
        upkeep=Stocks(manpower=3),
        before=RED_STOCKS,
        after=RED_STOCKS,
        lost_to_storage=Stocks(),
    ),
    EconomyResolved(
        player="red",
        income=3,
        upkeep=1,
        net=2,
        bank_before=0,
        bank_after=2,
        lost_to_cap=0,
    ),
    ConstructionStarted(
        player="red",
        building=Building.DEPOT,
        level=1,
        completes_on=9,
        cost=Stocks(steel=8),
    ),
    ConstructionCompleted(player="red", building=Building.BARRACKS, level=2),
    ConstructionVoided(
        player="red",
        building=Building.DEPOT,
        reason=ConstructionVoidedReason.INSUFFICIENT_STOCKS,
    ),
    RecruitmentVoided(
        owner="red",
        coord=SEEN,
        unit_type=INF,
        reason=RecruitmentVoidedReason.UNIT_CAP_REACHED,
    ),
    OrderMooted(
        unit_id=RED_SEEN, owner="red", reason=OrderMootedReason.DESTROYED_IN_BARRAGE
    ),
    TravelEnded(
        unit_id=RED_SEEN,
        owner="red",
        coord=SEEN,
        destination=FAR,
        reason=TravelEndReason.ARRIVED,
    ),
]


@pytest.mark.parametrize("event", PRIVATE_TO_RED, ids=lambda e: e.kind)
def test_private_bookkeeping_is_hidden_even_on_a_visible_hex(event: Event) -> None:
    assert _seen_by_blue(event) == []
    assert [_dump(e) for e in filter_events([event], _world(), "red")] == [_dump(event)]


# --- Private fields: the event is shown, without them --------------------------


def test_a_recruit_in_sight_shows_the_unit_but_not_the_cost_or_stocks() -> None:
    recruit = WorldUnitRecruited(
        unit_id=9,
        unit_type=INF,
        owner="red",
        coord=SEEN,
        cost=Stocks(manpower=3),
        stocks_after=RED_STOCKS,
    )
    [seen] = _seen_by_blue(recruit)
    assert seen == {
        "kind": "world_unit_recruited",
        "unit_id": 9,
        "unit_type": "infantry",
        "owner": "red",
        "coord": {"q": 2, "r": 0},
        "cost": None,
        "stocks_after": None,
    }
    [own] = filter_events([recruit], _world(), "red")
    assert isinstance(own, WorldUnitRecruitedView)
    assert own.cost == Stocks(manpower=3) and own.stocks_after == RED_STOCKS


def test_a_recruit_in_the_fog_is_not_shown() -> None:
    recruit = WorldUnitRecruited(
        unit_id=9,
        unit_type=INF,
        owner="red",
        coord=FAR,
        cost=Stocks(manpower=3),
        stocks_after=RED_STOCKS,
    )
    assert _seen_by_blue(recruit) == []


def test_a_disband_in_sight_hides_the_shortage_and_the_pick() -> None:
    disband = UnitDisbanded(
        unit_id=RED_SEEN,
        unit_type=INF,
        owner="red",
        coord=SEEN,
        shortage="manpower",
        chosen=True,
    )
    [seen] = _seen_by_blue(disband)
    assert seen["unit_id"] == RED_SEEN and seen["coord"] == {"q": 2, "r": 0}
    assert seen["shortage"] is None and seen["chosen"] is None
    [own] = filter_events([disband], _world(), "red")
    assert isinstance(own, UnitDisbandedView)
    assert own.shortage == "manpower" and own.chosen is True


def test_a_disband_in_the_fog_is_not_shown() -> None:
    disband = UnitDisbanded(
        unit_id=RED_FAR,
        unit_type=INF,
        owner="red",
        coord=FAR,
        shortage="fuel",
        chosen=False,
    )
    assert _seen_by_blue(disband) == []


def test_a_barrage_from_a_hidden_gun_hides_the_gun() -> None:
    # Red's gun in the fog shells blue's unit: blue learns the hit, not the gun.
    barrage = BarrageResolved(
        artillery_id=RED_GUN, target=Axial(q=0, r=0), hit=_hit(BLUE_UNIT, "blue")
    )
    [seen] = _seen_by_blue(barrage)
    assert seen["artillery_id"] is None
    assert seen["hit"]["unit_id"] == BLUE_UNIT
    [own] = filter_events([barrage], _world(), "red")
    assert isinstance(own, BarrageResolvedView) and own.artillery_id == RED_GUN


def test_a_barrage_on_a_hidden_unit_far_away_is_not_shown() -> None:
    barrage = BarrageResolved(
        artillery_id=RED_GUN, target=FAR, hit=_hit(RED_FAR, "red")
    )
    assert _seen_by_blue(barrage) == []


def test_a_barrage_by_a_gun_in_sight_names_the_gun() -> None:
    state = _world()
    gun_in_sight = state.fork_with_units(
        [
            u.model_copy(update={"coord": EDGE}) if u.id == RED_GUN else u
            for u in state.units
        ]
    )
    barrage = BarrageResolved(artillery_id=RED_GUN, target=SEEN, hit=None)
    [seen] = filter_events([barrage], gun_in_sight, "blue")
    assert seen.model_dump(mode="json")["artillery_id"] == RED_GUN


def test_the_owner_of_a_gun_that_died_still_sees_its_id() -> None:
    # The gun is gone from the end state; the hit record still names its owner.
    state = _world()
    no_gun = state.fork_with_units([u for u in state.units if u.id != RED_GUN])
    barrage = BarrageResolved(
        artillery_id=RED_GUN, target=SEEN, hit=_hit(RED_SEEN, "red")
    )
    killed = BarrageResolved(
        artillery_id=9, target=Axial(q=8, r=0), hit=_hit(RED_GUN, "red", ART)
    )
    seen = filter_events([killed, barrage], no_gun, "red")
    assert [e.model_dump(mode="json")["artillery_id"] for e in seen] == [None, RED_GUN]


# --- Unit movement: shown where the viewer can see the unit ---------------------


def test_a_move_into_sight_is_shown() -> None:
    move = UnitMoved(pulse=1, unit_id=RED_SEEN, origin=EDGE, dest=SEEN)
    assert _seen_by_blue(move) == [_dump(move)]


def test_a_move_in_the_fog_is_not_shown() -> None:
    move = UnitMoved(pulse=1, unit_id=RED_FAR, origin=Axial(q=10, r=0), dest=FAR)
    assert _seen_by_blue(move) == []


def test_a_blocked_step_from_the_fog_into_sight_is_not_shown() -> None:
    # The hex it tried to enter is visible, but the unit stood in the fog.
    blocked = MoveBlocked(
        pulse=1,
        unit_id=RED_FAR,
        origin=DARK,
        target=EDGE,
        reason=MoveBlockedReason.ZOC_LOCKED,
    )
    assert _seen_by_blue(blocked) == []


def test_a_blocked_step_in_sight_is_shown() -> None:
    blocked = MoveBlocked(
        pulse=1,
        unit_id=RED_SEEN,
        origin=SEEN,
        target=EDGE,
        reason=MoveBlockedReason.OCCUPIED,
    )
    assert _seen_by_blue(blocked) == [_dump(blocked)]


def test_a_column_waiting_in_the_fog_for_a_visible_hex_is_not_shown() -> None:
    held = ColumnHeld(pulse=3, unit_id=RED_FAR, coord=DARK, target=EDGE)
    assert _seen_by_blue(held) == []
    held_in_sight = ColumnHeld(pulse=3, unit_id=RED_SEEN, coord=SEEN, target=EDGE)
    assert _seen_by_blue(held_in_sight) == [_dump(held_in_sight)]


@pytest.mark.parametrize(
    "event",
    [
        UnitPacked(pulse=4, unit_id=RED_FAR, coord=FAR),
        ColumnStuck(pulse=4, unit_id=RED_FAR, coord=FAR, cause=DeployCause.CONTACT),
        UnitDeployed(
            pulse=4,
            unit_id=RED_FAR,
            origin=FAR,
            dest=FAR,
            cause=DeployCause.ARRIVED,
            disordered=False,
        ),
        UnitRepaired(unit_id=RED_FAR, hp_before=5, hp_after=8, in_settlement=False),
    ],
    ids=lambda e: e.kind,
)
def test_column_and_repair_events_in_the_fog_are_not_shown(event: Event) -> None:
    assert _seen_by_blue(event) == []


def test_a_jam_shows_only_the_jammed_units_in_sight() -> None:
    jam = TrafficJam(pulse=1, coord=EDGE, unit_ids=[RED_SEEN, RED_FAR])
    assert _seen_by_blue(jam) == [
        {"kind": "traffic_jam", "pulse": 1, "coord": {"q": 3, "r": 0}, "unit_ids": [2]}
    ]
    assert _seen_by_blue(TrafficJam(pulse=1, coord=FAR, unit_ids=[RED_FAR])) == []


def test_a_fight_in_sight_is_shown_whole_and_one_in_the_fog_not_at_all() -> None:
    fight = CombatResolved(
        pulse=1,
        coord=SEEN,
        participants=[
            _fighter(RED_SEEN, "red", CombatRole.DEFENDER),
            _fighter(7, "green", CombatRole.ATTACKER),
        ],
    )
    assert _seen_by_blue(fight) == [_dump(fight)]
    far_fight = fight.model_copy(update={"coord": FAR})
    assert _seen_by_blue(far_fight) == []


def test_a_capture_in_the_fog_is_not_shown() -> None:
    capture = SettlementCaptured(
        coord=FAR,
        settlement=Terrain.PLAINS,
        captured_by="red",
        previous_owner=None,
        unit_id=RED_FAR,
    )
    assert _seen_by_blue(capture) == []
    assert _seen_by_blue(capture.model_copy(update={"coord": SEEN})) != []


def test_dead_units_still_count_as_their_owners() -> None:
    # Blue's unit 5 moved in the fog and then died in a fight: it is gone from the
    # end state, but blue still gets its move.
    move = UnitMoved(pulse=1, unit_id=5, origin=Axial(q=10, r=0), dest=FAR)
    death = CombatResolved(
        pulse=2,
        coord=FAR,
        participants=[
            _fighter(5, "blue", CombatRole.ATTACKER),
            _fighter(RED_FAR, "red", CombatRole.DEFENDER),
        ],
    )
    assert filter_events([move, death], _world(), "blue") == [move, death]


def test_the_view_models_mirror_the_engine_events() -> None:
    pairs: list[tuple[type[BaseModel], type[BaseModel]]] = [
        (BarrageResolved, BarrageResolvedView),
        (UnitDisbanded, UnitDisbandedView),
        (WorldUnitRecruited, WorldUnitRecruitedView),
    ]
    for engine, view in pairs:
        assert list(view.model_fields) == list(engine.model_fields)


# --- The state ------------------------------------------------------------------


def test_the_state_hides_the_other_players_economy_base_and_orders() -> None:
    view = redact_state(_world(), "blue")
    red = next(p for p in view.players if p.id == "red")
    assert red == Player(id="red")
    blue = next(p for p in view.players if p.id == "blue")
    assert blue.stocks == Stocks(manpower=5)
    assert view.standing_orders == []
    assert {u.id for u in view.units} == {BLUE_UNIT, RED_SEEN}
    # Red's own view keeps everything of red's.
    own = redact_state(_world(), "red")
    assert next(p for p in own.players if p.id == "red").stocks == RED_STOCKS
    assert [o.unit_id for o in own.standing_orders] == [RED_FAR]


def test_a_visible_enemy_unit_shows_only_what_is_on_the_board() -> None:
    view = redact_state(_world(), "blue")
    red = next(u for u in view.units if u.id == RED_SEEN)
    assert set(red.model_dump()) == {
        "id",
        "type",
        "owner",
        "coord",
        "hp",
        "facing",
        "posture",
        "disordered",
    }
    assert red.posture is Posture.DEPLOYED
