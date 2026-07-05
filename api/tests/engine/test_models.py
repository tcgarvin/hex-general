"""Tests for the domain models and JSON round-tripping (roadmap P0).

The headline guarantee: a hand-written `GameState` serializes and reloads
identically, and the `Order` union validates back to the right concrete type.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.engine.hex_coords import Axial
from app.engine.models import (
    BarrageOrder,
    GameConfig,
    GameMap,
    GameState,
    Hex,
    HoldOrder,
    MoveOrder,
    Order,
    Player,
    RestOrder,
    Settlement,
    Unit,
    VictoryConfig,
)
from app.engine.stats import Terrain, UnitType

ORDER_ADAPTER: TypeAdapter[Order] = TypeAdapter(Order)


def sample_state() -> GameState:
    """A small but structurally complete two-player state for round-trip tests."""
    return GameState(
        turn=3,
        map=GameMap(
            width=3,
            height=2,
            hexes=[
                Hex(coord=Axial(q=0, r=0), terrain=Terrain.CITY),
                Hex(coord=Axial(q=1, r=0), terrain=Terrain.WOODS),
                Hex(coord=Axial(q=2, r=0), terrain=Terrain.TOWN, road=True),
                Hex(coord=Axial(q=0, r=1), terrain=Terrain.HILLS),
                Hex(coord=Axial(q=1, r=1), terrain=Terrain.RIVER),
                Hex(coord=Axial(q=2, r=1), terrain=Terrain.PLAINS, road=True),
            ],
        ),
        settlements=[
            Settlement(coord=Axial(q=0, r=0), controlled_by="blue"),
            Settlement(coord=Axial(q=2, r=0)),
        ],
        units=[
            Unit(
                id=1,
                type=UnitType.INFANTRY,
                owner="blue",
                coord=Axial(q=0, r=1),
                hp=10,
            ),
            Unit(
                id=2,
                type=UnitType.ARMOR,
                owner="red",
                coord=Axial(q=2, r=1),
                hp=7,
            ),
        ],
        players=[Player(id="blue", bank=5), Player(id="red", bank=12)],
        config=GameConfig(victory=VictoryConfig(turn_limit=20)),
    )


def test_game_state_json_round_trip() -> None:
    state = sample_state()
    reloaded = GameState.model_validate_json(state.model_dump_json())
    assert reloaded == state


def test_neutral_settlement_defaults_to_uncontrolled() -> None:
    settlement = Settlement(coord=Axial(q=2, r=0))
    assert settlement.controlled_by is None


def test_lookup_helpers() -> None:
    state = sample_state()

    river_hex = state.hex_at(Axial(q=1, r=1))
    assert river_hex is not None and river_hex.terrain is Terrain.RIVER
    assert state.hex_at(Axial(q=9, r=9)) is None

    unit = state.unit_at(Axial(q=0, r=1))
    assert unit is not None and unit.id == 1
    assert state.unit_at(Axial(q=1, r=0)) is None

    city = state.settlement_at(Axial(q=0, r=0))
    assert city is not None and city.controlled_by == "blue"
    city_hex = state.hex_at(city.coord)
    assert city_hex is not None and city_hex.terrain is Terrain.CITY
    assert state.settlement_at(Axial(q=1, r=1)) is None


def test_unit_hp_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Unit(id=3, type=UnitType.INFANTRY, owner="blue", coord=Axial(q=0, r=0), hp=11)
    with pytest.raises(ValidationError):
        Unit(id=4, type=UnitType.INFANTRY, owner="blue", coord=Axial(q=0, r=0), hp=-1)


def test_player_bank_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Player(id="blue", bank=21)
    with pytest.raises(ValidationError):
        Player(id="blue", bank=-1)


def test_game_state_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GameState.model_validate(
            {
                "turn": 0,
                "map": {"width": 1, "height": 1, "hexes": []},
                "config": {"victory": {"turn_limit": 10}},
                "bogus": True,
            }
        )


@pytest.mark.parametrize(
    "order",
    [
        MoveOrder(unit_id=1, path=[Axial(q=0, r=0), Axial(q=1, r=0)]),
        BarrageOrder(unit_id=2, target=Axial(q=4, r=4)),
        RestOrder(unit_id=3),
        HoldOrder(unit_id=4),
    ],
)
def test_order_union_round_trip(order: Order) -> None:
    reloaded = ORDER_ADAPTER.validate_json(ORDER_ADAPTER.dump_json(order))
    assert reloaded == order
    assert type(reloaded) is type(order)


def test_order_union_dispatches_on_kind() -> None:
    parsed = ORDER_ADAPTER.validate_python(
        {"kind": "barrage", "unit_id": 7, "target": {"q": 1, "r": 1}}
    )
    assert isinstance(parsed, BarrageOrder)
    assert parsed.target == Axial(q=1, r=1)


def test_move_order_requires_nonempty_path() -> None:
    with pytest.raises(ValidationError):
        MoveOrder(unit_id=1, path=[])


def test_state_with_units_lacking_facing_still_validates() -> None:
    # State JSONB persisted before the `facing` field existed must keep loading:
    # a unit dict without `facing` validates with the default (roadmap U0).
    state = GameState.model_validate(
        {
            "turn": 0,
            "map": {"width": 1, "height": 1, "hexes": []},
            "units": [
                {
                    "id": 1,
                    "type": "infantry",
                    "owner": "blue",
                    "coord": {"q": 0, "r": 0},
                    "hp": 10,
                }
            ],
            "config": {"victory": {"turn_limit": 10}},
        }
    )
    assert state.units[0].facing == "right"
