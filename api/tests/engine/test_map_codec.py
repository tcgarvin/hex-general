"""Compact map encoding (WORLD_SPEC §10.1): exact round trips, loud failures."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engine.hex_coords import Axial, axial_to_offset, offset_to_axial
from app.engine.economy_rules import SiteKind
from app.engine.map_codec import CompactMap, MapCodecError, decode_map, encode_map
from app.engine.models import GameMap, Hex, MapSite
from app.engine.stats import Terrain
from app.world.generate import WorldParams, generate


def _offset_map(rows: list[str], roads: list[str]) -> GameMap:
    chars = {
        ".": Terrain.PLAINS,
        "w": Terrain.WOODS,
        "~": Terrain.RIVER,
        "o": Terrain.LAKE,
    }
    return GameMap(
        width=len(rows[0]),
        height=len(rows),
        hexes=[
            Hex(
                coord=offset_to_axial(col, row),
                terrain=chars[char],
                road=roads[row][col] == "#",
            )
            for row, line in enumerate(rows)
            for col, char in enumerate(line)
        ],
    )


def test_axial_to_offset_inverts_offset_to_axial() -> None:
    for col in range(-3, 6):
        for row in range(-3, 6):
            assert axial_to_offset(offset_to_axial(col, row)) == (col, row)


def test_small_map_round_trips_and_encodes_row_major() -> None:
    game_map = _offset_map([".w~", "w.."], ["##.", ".#."])
    compact = encode_map(game_map)
    assert compact.terrain == ".w~w.."
    assert compact.roads == "##..#."
    assert decode_map(compact).model_dump() == game_map.model_dump()


def test_generated_world_round_trips() -> None:
    world = generate(WorldParams(slots=4, width=60, height=60, seed=3))
    decoded = decode_map(encode_map(world.game_map))
    assert decoded.hexes == world.game_map.hexes
    assert decoded.sites == world.game_map.sites
    assert (decoded.width, decoded.height) == (60, 60)


def test_site_levels_round_trip_through_json() -> None:
    game_map = _offset_map(["...", "..."], ["...", "..."]).model_copy(
        update={
            "sites": [
                MapSite(coord=offset_to_axial(0, 0), kind=SiteKind.BASE, garrison=0),
                MapSite(
                    coord=offset_to_axial(2, 1),
                    kind=SiteKind.FARM,
                    garrison=1,
                    level=3,
                ),
            ]
        }
    )
    payload = encode_map(game_map).model_dump_json()
    decoded = decode_map(CompactMap.model_validate_json(payload))
    assert [s.level for s in decoded.sites] == [1, 3]
    assert decoded.sites == game_map.sites


def test_a_stored_map_without_site_levels_decodes_at_level_one() -> None:
    # Maps stored before levels existed: sites on town hexes, no ``level`` key.
    payload = {
        "width": 2,
        "height": 1,
        "terrain": "t.",
        "roads": "..",
        "sites": [{"coord": {"q": 0, "r": 0}, "kind": "farm", "garrison": 2}],
    }
    decoded = decode_map(CompactMap.model_validate(payload))
    assert decoded.sites[0].level == 1
    assert decoded.index[offset_to_axial(0, 0)].terrain is Terrain.TOWN


def test_encode_rejects_a_map_that_is_not_a_row_major_rectangle() -> None:
    game_map = _offset_map([".."], [".."])
    shuffled = GameMap(width=2, height=1, hexes=list(reversed(game_map.hexes)))
    with pytest.raises(MapCodecError, match="row-major"):
        encode_map(shuffled)
    holed = GameMap(width=2, height=1, hexes=game_map.hexes[:1])
    with pytest.raises(MapCodecError, match="1 hexes"):
        encode_map(holed)
    axial = GameMap(
        width=1, height=1, hexes=[Hex(coord=Axial(q=1, r=0), terrain=Terrain.PLAINS)]
    )
    with pytest.raises(MapCodecError):
        encode_map(axial)


@pytest.mark.parametrize(
    ("terrain", "roads", "message"),
    [
        (".w", "..", "terrain grid has 2 chars"),
        ("...", "..", "roads grid has 2 chars"),
        (".?.", "...", "unknown terrain"),
        ("...", ".x.", "unknown road"),
    ],
)
def test_compact_map_validates_shape_and_alphabet(
    terrain: str, roads: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        CompactMap(width=3, height=1, terrain=terrain, roads=roads)


def test_lake_round_trips_through_the_codec() -> None:
    game_map = _offset_map([".o~", "w.."], ["...", ".#."])
    compact = encode_map(game_map)
    assert compact.terrain == ".o~w.."
    assert decode_map(compact).model_dump() == game_map.model_dump()
