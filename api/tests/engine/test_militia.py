"""Militia: the neutral garrisons' script, recovery, muster and retake (WORLD_SPEC §7.1)."""

from __future__ import annotations

from app.engine.economy_rules import SiteKind, WorldEconomy
from app.engine.events import MilitiaMustered, SettlementCaptured, UnitRepaired
from app.engine.execution import resolve_execution
from app.engine.fog import redact_state
from app.engine.hex_coords import AXIAL_DIRECTIONS, Axial
from app.engine.militia import assign_posts, militia_orders
from app.engine.models import (
    NEUTRAL,
    BarrageOrder,
    GameConfig,
    GameMap,
    GameState,
    Hex,
    HoldOrder,
    MapSite,
    MoveOrder,
    Player,
    Posture,
    Settlement,
    Unit,
    VictoryConfig,
)
from app.engine.stats import Terrain, UnitType

INF, ART = UnitType.INFANTRY, UnitType.ARTILLERY
SITE = Axial(q=6, r=6)
BASE = Axial(q=1, r=1)
OTHER = Axial(q=12, r=6)  # a second farm, for the post backfill


def ring(direction: int, steps: int = 1) -> Axial:
    """The hex ``steps`` out from the site in ``AXIAL_DIRECTIONS[direction]``."""
    d = AXIAL_DIRECTIONS[direction]
    return Axial(q=SITE.q + steps * d.q, r=SITE.r + steps * d.r)


def world(
    units: list[Unit],
    tier: int = 4,
    held_by: str | None = None,
    terrain: dict[Axial, Terrain] | None = None,
    clock: int = 0,
) -> GameState:
    """A 16×13 plains world: player ``p``'s base and one farm of ``tier`` at SITE."""
    terrain = terrain or {}
    hexes = [
        Hex(coord=Axial(q=q, r=r), terrain=terrain.get(Axial(q=q, r=r), Terrain.PLAINS))
        for q in range(16)
        for r in range(13)
    ]
    sites = [
        MapSite(coord=BASE, kind=SiteKind.BASE, garrison=0),
        MapSite(coord=SITE, kind=SiteKind.FARM, garrison=tier),
        MapSite(coord=OTHER, kind=SiteKind.FARM, garrison=1),
    ]
    return GameState(
        turn=1,
        map=GameMap(width=16, height=13, hexes=hexes, sites=sites),
        settlements=[
            Settlement(coord=BASE, controlled_by="p"),
            Settlement(coord=SITE, controlled_by=held_by, muster_clock=clock),
            Settlement(coord=OTHER, controlled_by="p"),  # held: never musters
        ],
        units=units,
        players=[Player(id="p")],
        config=GameConfig(
            economy=WorldEconomy(),
            victory=VictoryConfig(turn_limit=10**6, city_elimination=False),
        ),
    )


def militia(uid: int, coord: Axial, kind: UnitType = INF, hp: int = 10) -> Unit:
    return Unit(id=uid, type=kind, owner=NEUTRAL, coord=coord, hp=hp, post=SITE)


def player(uid: int, coord: Axial, hp: int = 10, kind: UnitType = INF) -> Unit:
    return Unit(id=uid, type=kind, owner="p", coord=coord, hp=hp)


def orders_of(state: GameState) -> dict[int, object]:
    return {o.unit_id: o for o in militia_orders(state)}


def site_of(state: GameState) -> Settlement:
    return next(s for s in state.settlements if s.coord == SITE)


def unit_of(state: GameState, uid: int) -> Unit:
    return next(u for u in state.units if u.id == uid)


# --- The script ---------------------------------------------------------------


def test_the_keeper_holds_with_intruders_beside_it() -> None:
    state = world([militia(10, SITE), player(1, ring(0)), player(2, ring(3))])
    assert orders_of(state) == {10: HoldOrder(unit_id=10)}


def test_a_picket_holds_with_no_intruder_in_the_zone() -> None:
    state = world([militia(10, SITE), militia(11, ring(0)), player(1, ring(0, 2))])
    assert orders_of(state)[11] == HoldOrder(unit_id=11)


def test_a_picket_attacks_an_adjacent_intruder() -> None:
    state = world([militia(10, SITE), militia(11, ring(0)), player(1, ring(1))])
    assert orders_of(state)[11] == MoveOrder(unit_id=11, path=[ring(1)])


def test_a_picket_takes_a_two_step_ring_path_to_its_intruder() -> None:
    state = world([militia(10, SITE), militia(11, ring(0)), player(1, ring(2))])
    assert orders_of(state)[11] == MoveOrder(unit_id=11, path=[ring(1), ring(2)])


def test_a_picket_out_of_reach_closes_in_through_the_ring() -> None:
    state = world([militia(10, SITE), militia(11, ring(0)), player(1, ring(3))])
    order = orders_of(state)[11]
    assert order == MoveOrder(unit_id=11, path=[ring(1), ring(2)])


def test_a_picket_never_leaves_the_zone_or_chases_outside_it() -> None:
    state = world([militia(10, SITE), militia(11, ring(0)), player(1, ring(0, 2))])
    assert orders_of(state)[11] == HoldOrder(unit_id=11)  # beside it, but outside
    resolved, _ = resolve_execution(state, [])
    assert unit_of(resolved, 11).coord == ring(0)


def test_a_picket_obeys_zoc_and_goes_round_the_other_way() -> None:
    # The picket starts beside a player unit outside the zone, so it may not step
    # into ring(1), which is in the intruder's ZOC; it closes in the other way.
    outside = ring(0, 2)
    state = world(
        [
            militia(10, SITE),
            militia(11, ring(0)),
            player(1, ring(2)),
            player(2, outside),
        ]
    )
    assert orders_of(state)[11] == MoveOrder(unit_id=11, path=[ring(5), ring(4)])


def test_a_picket_prefers_the_intruder_on_the_site_hex() -> None:
    # Unit 10 goes home onto the site (attacking); picket 11 also picks the site
    # over the intruder at ring(2), though both are one step away.
    state = world(
        [
            militia(10, ring(0)),
            militia(11, ring(3)),
            player(1, SITE),
            player(2, ring(2)),
        ]
    )
    orders = orders_of(state)
    assert orders[10] == MoveOrder(unit_id=10, path=[SITE])
    assert orders[11] == MoveOrder(unit_id=11, path=[SITE])


def test_a_picket_breaks_ties_by_lowest_hp_then_lowest_id() -> None:
    weak = world(
        [
            militia(10, SITE),
            militia(11, ring(0)),
            player(2, ring(1), hp=8),
            player(3, ring(5), hp=4),
        ]
    )
    assert orders_of(weak)[11] == MoveOrder(unit_id=11, path=[ring(5)])
    even = world(
        [
            militia(10, SITE),
            militia(11, ring(0)),
            player(3, ring(5)),
            player(2, ring(1)),
        ]
    )
    assert orders_of(even)[11] == MoveOrder(unit_id=11, path=[ring(1)])


def test_with_the_keeper_dead_the_nearest_infantry_goes_home() -> None:
    state = world([militia(11, ring(3)), militia(12, ring(0))])
    assert orders_of(state) == {
        11: MoveOrder(unit_id=11, path=[SITE]),
        12: HoldOrder(unit_id=12),
    }


def test_a_gun_holds_until_an_intruder_is_in_range() -> None:
    quiet = world([militia(10, SITE), militia(12, ring(3), ART), player(1, ring(0, 2))])
    assert orders_of(quiet)[12] == HoldOrder(unit_id=12)
    alarmed = world([militia(10, SITE), militia(12, ring(3), ART), player(1, ring(0))])
    assert orders_of(alarmed)[12] == BarrageOrder(unit_id=12, target=ring(0))


def test_a_gun_fires_at_the_site_hex_first_then_at_the_weakest() -> None:
    on_site = world(
        [militia(12, ring(3), ART), player(1, SITE, hp=9), player(2, ring(1), hp=2)]
    )
    assert orders_of(on_site)[12] == BarrageOrder(unit_id=12, target=SITE)
    weakest = world(
        [
            militia(10, SITE),
            militia(12, ring(3), ART),
            player(1, ring(1), hp=9),
            player(2, ring(5), hp=2),
        ]
    )
    assert orders_of(weakest)[12] == BarrageOrder(unit_id=12, target=ring(5))


def test_militia_outside_the_zone_hold_but_a_gun_there_still_fires() -> None:
    state = world(
        [
            militia(10, SITE),
            militia(11, ring(0, 2)),
            militia(12, ring(3, 2), ART),
            player(1, ring(0)),
        ]
    )
    orders = orders_of(state)
    assert orders[11] == HoldOrder(unit_id=11)
    assert orders[12] == BarrageOrder(unit_id=12, target=ring(0))


def test_the_script_is_deterministic() -> None:
    units = [militia(10, SITE), militia(11, ring(0)), militia(12, ring(3), ART)]
    units += [player(1, ring(2)), player(2, ring(4), hp=3)]
    assert militia_orders(world(units)) == militia_orders(world(units))
    first, first_events = resolve_execution(world(units), [])
    second, second_events = resolve_execution(world(units), [])
    assert first == second and first_events == second_events


def test_a_triggered_picket_really_attacks_in_the_turn() -> None:
    state = world([militia(10, SITE), militia(11, ring(0)), player(1, ring(1), hp=1)])
    resolved, _ = resolve_execution(state, [])
    assert unit_of(resolved, 11).coord == ring(1)
    assert all(u.owner == NEUTRAL for u in resolved.units)


# --- Recovery -------------------------------------------------------------------


def test_militia_recover_one_hp_a_turn_up_to_their_starting_hp() -> None:
    state = world([militia(10, SITE, hp=8)], tier=2)
    state, events = resolve_execution(state, [])
    assert unit_of(state, 10).hp == 9
    assert any(isinstance(e, UnitRepaired) and e.unit_id == 10 for e in events)
    state, _ = resolve_execution(state, [])
    state, events = resolve_execution(state, [])
    assert unit_of(state, 10).hp == 10
    assert not any(isinstance(e, UnitRepaired) for e in events)


def test_a_tier_one_militia_recovers_only_to_five_hp() -> None:
    state = world([militia(10, SITE, hp=4)], tier=1)
    state, _ = resolve_execution(state, [])
    state, _ = resolve_execution(state, [])
    assert unit_of(state, 10).hp == 5


def test_a_militia_unit_damaged_this_turn_does_not_recover() -> None:
    state = world([militia(10, SITE, hp=9), player(1, ring(0))], tier=2)
    resolved, _ = resolve_execution(state, [MoveOrder(unit_id=1, path=[SITE])])
    assert unit_of(resolved, 10).hp < 9


def test_militia_of_a_player_held_site_do_not_recover() -> None:
    state = world([militia(11, ring(0, 2), hp=6)], held_by="p")
    resolved, events = resolve_execution(state, [])
    assert unit_of(resolved, 11).hp == 6
    assert not any(isinstance(e, UnitRepaired) for e in events)


def test_militia_recover_again_once_they_retake_their_site() -> None:
    state = world([militia(11, ring(0), hp=6)], held_by="p")
    resolved, _ = resolve_execution(state, [])  # goes home, retakes the site
    assert unit_of(resolved, 11).coord == SITE
    assert site_of(resolved).controlled_by is None
    assert unit_of(resolved, 11).hp == 7


# --- Muster ---------------------------------------------------------------------


def test_a_site_under_strength_musters_the_keeper_after_four_quiet_turns() -> None:
    state = world([], tier=2)
    for clock in (1, 2, 3):
        state, events = resolve_execution(state, [])
        assert site_of(state).muster_clock == clock
        assert not any(isinstance(e, MilitiaMustered) for e in events)
    state, events = resolve_execution(state, [])
    mustered = [e for e in events if isinstance(e, MilitiaMustered)]
    assert len(mustered) == 1
    keeper = unit_of(state, mustered[0].unit_id)
    assert (keeper.coord, keeper.post, keeper.type, keeper.hp) == (SITE, SITE, INF, 10)
    assert site_of(state).muster_clock == 0


def test_a_player_unit_in_the_zone_pauses_the_count() -> None:
    scout = player(1, ring(0))
    scout.posture = Posture.COLUMN  # even a packed column
    state = world([scout], tier=2, clock=2)
    resolved, _ = resolve_execution(state, [])
    assert site_of(resolved).muster_clock == 2


def test_the_keeper_slot_comes_back_first_on_the_site_hex() -> None:
    state = world([], tier=3, clock=3)  # both the infantry and the gun are lost
    _, events = resolve_execution(state, [])
    mustered = [e for e in events if isinstance(e, MilitiaMustered)]
    assert [(e.unit_type, e.coord) for e in mustered] == [(INF, SITE)]


def test_a_lost_second_infantry_returns_on_the_empty_site_hex() -> None:
    # Tier 4 keeps one infantry (outside the zone, so it never goes home) and
    # its gun; the infantry it lost comes back on the site hex.
    state = world([militia(11, ring(1, 2)), militia(12, ring(3), ART)], clock=3)
    _, events = resolve_execution(state, [])
    mustered = next(e for e in events if isinstance(e, MilitiaMustered))
    assert (mustered.unit_type, mustered.coord, mustered.hp) == (INF, SITE, 10)


def test_a_gun_musters_on_the_first_free_ring_hex() -> None:
    state = world([militia(10, SITE)], tier=3, clock=3)
    resolved, events = resolve_execution(state, [])
    mustered = next(e for e in events if isinstance(e, MilitiaMustered))
    free_ring = sorted((ring(d) for d in range(6)), key=lambda c: (c.q, c.r))
    assert (mustered.unit_type, mustered.coord) == (ART, free_ring[0])


def test_a_muster_waits_when_there_is_no_room() -> None:
    towns = {ring(d): Terrain.TOWN for d in range(6)}
    state = world([militia(10, SITE)], tier=3, terrain=towns, clock=3)
    for _ in range(2):
        state, events = resolve_execution(state, [])
        assert not any(isinstance(e, MilitiaMustered) for e in events)
        assert site_of(state).muster_clock == 4


def test_a_player_held_site_musters_nothing() -> None:
    state = world([], tier=2, held_by="p", clock=0)
    for _ in range(5):
        state, events = resolve_execution(state, [])
        assert not any(isinstance(e, MilitiaMustered) for e in events)
    assert site_of(state).muster_clock == 0


def test_a_site_that_reverts_to_neutral_musters_its_militia_back() -> None:
    state = world([], tier=2, held_by="p")
    state, _ = resolve_execution(state, [])
    site_of(state).controlled_by = None  # its owner left the world
    events: list[object] = []
    for _ in range(4):
        state, turn_events = resolve_execution(state, [])
        events += turn_events
    assert [e.coord for e in events if isinstance(e, MilitiaMustered)] == [SITE]


# --- After the site falls -----------------------------------------------------


def test_militia_on_their_own_held_site_return_it_to_neutral() -> None:
    state = world([militia(10, SITE)], held_by="p")
    resolved, events = resolve_execution(state, [])
    assert site_of(resolved).controlled_by is None
    retaken = next(e for e in events if isinstance(e, SettlementCaptured))
    assert (retaken.captured_by, retaken.previous_owner, retaken.unit_id) == (
        NEUTRAL,
        "p",
        10,
    )


def test_surviving_militia_keep_attacking_the_unit_on_the_taken_site() -> None:
    state = world([militia(11, ring(0)), player(1, SITE)], held_by="p")
    assert orders_of(state)[11] == MoveOrder(unit_id=11, path=[SITE])


# --- Fog and stored states ----------------------------------------------------


def test_fog_hides_militia_and_their_count_out_of_sight() -> None:
    hidden = world([militia(10, SITE), player(1, Axial(q=15, r=12))], clock=2)
    view = redact_state(hidden, "p")
    assert [u.id for u in view.units] == [1]
    assert site_of(view).muster_clock == 0  # not in sight
    assert site_of(hidden).muster_clock == 2  # the true state is untouched
    seen = world([militia(10, SITE), player(1, ring(0, 2))], clock=2)
    view = redact_state(seen, "p")
    assert {u.id for u in view.units} == {1, 10}
    assert site_of(view).muster_clock == 2


def test_assign_posts_gives_each_old_militia_unit_its_nearest_garrisoned_site() -> None:
    near_other = Axial(q=11, r=6)
    halfway = Axial(q=9, r=6)  # three from each farm: the first listed wins
    units = [
        Unit(id=10, type=INF, owner=NEUTRAL, coord=ring(0), hp=10),
        Unit(id=11, type=INF, owner=NEUTRAL, coord=near_other, hp=10),
        Unit(id=12, type=INF, owner=NEUTRAL, coord=halfway, hp=10),
        player(1, BASE),
    ]
    posted = assign_posts(world(units))
    assert [u.post for u in posted.units] == [SITE, OTHER, SITE, None]


def test_assign_posts_leaves_a_state_with_posts_alone() -> None:
    state = world([militia(10, SITE)])
    assert assign_posts(state) is state
