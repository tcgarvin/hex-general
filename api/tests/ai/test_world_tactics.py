"""World bot tactics on small hand-built states: sieges, rest, travel, recruits.

Each test sets up one situation and asserts on the shape of the bot's plan
(who attacks, where a column stops), not on exact paths.
"""

from __future__ import annotations

from app.ai.intel import Intel
from app.ai.world import (
    Personality,
    ScriptedStrategist,
    Stance,
    StrategistArgs,
    plan_world_turn,
    siege_group,
)
from app.engine.economy_rules import Building, SiteKind, Stocks
from app.engine.fog import redact_state
from app.engine.hex_coords import Axial, distance, neighbors
from app.engine.models import (
    BarrageOrder,
    GameState,
    MoveOrder,
    Order,
    RestOrder,
    TravelOrder,
    Unit,
)
from app.games.validation import validate_submission
from ai.world_fixtures import (
    ART,
    INF,
    at,
    garrison,
    make_unit,
    small_world,
)

BASE = at(1, 1)
FARM = at(9, 6)
EXPAND = StrategistArgs(stance=Stance.EXPAND)


def _plan(state: GameState, args: StrategistArgs = EXPAND) -> dict[int, Order]:
    view = redact_state(state, "p")
    plan = plan_world_turn(view, "p", args)
    validate_submission(view, "p", plan.orders, plan.recruitments, None)
    return {o.unit_id: o for o in plan.orders}


def _strikes(order: Order, target: Axial) -> bool:
    return isinstance(order, MoveOrder) and order.path[-1] == target


def _farm_world(
    units: list[Unit],
    towns: tuple[Axial, ...] = (),
    buildings: dict[Building, int] | None = None,
) -> GameState:
    return small_world(
        {BASE: (SiteKind.BASE, 0), FARM: (SiteKind.FARM, 2)},
        [garrison(100, FARM), *units],
        held={BASE: "p"},
        towns=towns,
        buildings=buildings,
    )


def test_a_siege_group_strikes_the_garrison_together() -> None:
    sides = neighbors(FARM)[:3]
    state = _farm_world([make_unit(k + 1, INF, c) for k, c in enumerate(sides)])
    orders = _plan(state)
    assert all(_strikes(orders[k + 1], FARM) for k in range(3))


def test_artillery_fires_on_the_besieged_site_not_a_bystander() -> None:
    other = at(13, 6)  # another farm's garrison, in the gun's range but far from FARM
    gun_hex = at(11, 6)
    assert distance(gun_hex, other) <= 3 and distance(gun_hex, FARM) <= 3
    assert distance(other, FARM) > 3
    state = small_world(
        {BASE: (SiteKind.BASE, 0), FARM: (SiteKind.FARM, 2), other: (SiteKind.FARM, 1)},
        [
            garrison(100, FARM),
            garrison(101, other, hp=5),
            # The siege group stands on the far side from the bystander.
            make_unit(1, INF, neighbors(FARM)[2]),
            make_unit(2, INF, neighbors(FARM)[3]),
            make_unit(4, INF, neighbors(FARM)[4]),
            make_unit(3, ART, gun_hex),
        ],
        held={BASE: "p"},
    )
    orders = _plan(state)
    gun = orders[3]
    assert isinstance(gun, BarrageOrder) and gun.target == FARM


def test_a_worn_unit_leaves_the_siege_to_rest_on_a_town() -> None:
    worn_at = neighbors(FARM)[0]
    town = next(
        c
        for c in neighbors(worn_at)
        if c != FARM and distance(c, FARM) == 2 and c.q >= 0 and c.r >= 0
    )
    state = _farm_world(
        [make_unit(1, INF, worn_at, hp=3), make_unit(2, INF, neighbors(FARM)[3])],
        towns=(town,),
    )
    order = _plan(state)[1]
    assert isinstance(order, MoveOrder) and order.path[-1] == town


def test_a_worn_unit_on_a_town_rests_beside_a_garrison() -> None:
    town = neighbors(FARM)[0]
    state = _farm_world([make_unit(1, INF, town, hp=6)], towns=(town,))
    assert isinstance(_plan(state)[1], RestOrder)


def test_a_unit_too_weak_to_survive_the_return_fire_does_not_attack() -> None:
    state = _farm_world([make_unit(1, INF, neighbors(FARM)[0], hp=1)])
    assert not _strikes(_plan(state)[1], FARM)


def test_a_column_stops_short_of_a_garrison_and_deploys_clean() -> None:
    far = at(9, 0)
    farm = at(9, 11)
    state = small_world(
        {far: (SiteKind.BASE, 0), farm: (SiteKind.FARM, 1)},
        [make_unit(1, INF, far), garrison(100, farm)],
        held={far: "p"},
        stocks=Stocks(fuel=20, steel=20),  # nothing to recruit with
    )
    order = _plan(state)[1]
    assert isinstance(order, TravelOrder)
    assert distance(order.destination, farm) > 3


def test_a_unit_on_the_base_steps_off_for_a_recruit() -> None:
    state = _farm_world(
        [make_unit(1, INF, BASE), make_unit(2, INF, at(3, 3))],
        buildings={Building.BARRACKS: 1},
    )
    view = redact_state(state, "p")
    plan = plan_world_turn(
        view, "p", StrategistArgs(stance=Stance.DEFEND, reserve_share=0.5)
    )
    validate_submission(view, "p", plan.orders, plan.recruitments, None)
    assert [r.coord for r in plan.recruitments] == [BASE]
    order = next(o for o in plan.orders if o.unit_id == 1)
    assert isinstance(order, MoveOrder) and order.path[-1] != BASE


def test_neutral_garrisons_never_make_a_cautious_bot_defend() -> None:
    state = _farm_world([make_unit(1, INF, at(2, 2))])
    state.units.append(garrison(101, at(2, 3)))  # a stray garrison unit by the base
    cautious = Personality(aggression=0.2, reach=20, garrison_share=0.0)
    args = ScriptedStrategist(cautious).choose(redact_state(state, "p"), "p")
    assert args.stance is Stance.EXPAND


def test_siege_groups_grow_with_the_garrison() -> None:
    sizes = [siege_group(hp) for hp in (0, 5, 10, 20, 30)]
    assert sizes[0] == (1, 1)
    assert all(want >= least for want, least in sizes)
    wants = [want for want, _ in sizes]
    assert wants == sorted(wants) and wants[-1] > wants[1]


def test_intel_assumes_an_unseen_garrison_and_reads_a_seen_one() -> None:
    near = at(3, 2)
    state = small_world(
        {BASE: (SiteKind.BASE, 0), near: (SiteKind.FARM, 1), FARM: (SiteKind.FARM, 3)},
        [make_unit(1, INF, at(3, 1)), garrison(100, near, hp=4)],
        held={BASE: "p"},
    )
    intel = Intel(redact_state(state, "p"), "p")
    assert intel.garrisons[near].seen and intel.garrisons[near].hp == 4
    assert not intel.garrisons[FARM].seen and intel.garrisons[FARM].hp == 20
    assert not intel.column_safe(FARM)
    assert intel.column_safe(at(15, 0))
