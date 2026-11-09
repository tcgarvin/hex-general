"""Compact storage encoding for a `GameMap` (WORLD_SPEC §10.1).

A world map is ~10^5 hexes; as a list of hex objects its JSON is ~7 MB, while
the same map as two character grids is ~250 KB. `CompactMap` is that grid form:
the terrain and road layers as row-major strings over the odd-q offset rectangle,
one character per hex, using the scenario format's alphabet (`TERRAIN_CHARS`,
`ROAD_CHAR`/`NO_ROAD_CHAR`). It is how the `game_maps` table stores a map once per
world, instead of in every state row, and the form the client fetches it in.

`encode_map` only accepts a map that is exactly a full offset rectangle in
row-major order, which is what scenarios (with offset layout) and the world
generator build. `CompactMap` validates its own shape and alphabet, so it is the
boundary for map data read back from the database. `decode_map` then rebuilds an
equal `GameMap` with hexes in the same order.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.engine.hex_coords import axial_to_offset, offset_to_axial
from app.engine.models import GameMap, Hex
from app.engine.scenario import NO_ROAD_CHAR, ROAD_CHAR, TERRAIN_CHARS, TERRAIN_TO_CHAR


class MapCodecError(ValueError):
    """A map cannot be encoded: it is not a full row-major offset rectangle."""


class CompactMap(BaseModel):
    """A map as two row-major offset grids flattened to strings.

    ``terrain[row * width + col]`` is the terrain character of grid cell
    ``(col, row)``; ``roads`` is the same shape with `ROAD_CHAR` for a road and
    `NO_ROAD_CHAR` for none.
    """

    model_config = ConfigDict(frozen=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    terrain: str
    roads: str

    @model_validator(mode="after")
    def _check_grids(self) -> CompactMap:
        size = self.width * self.height
        for name, grid in (("terrain", self.terrain), ("roads", self.roads)):
            if len(grid) != size:
                raise ValueError(
                    f"{name} grid has {len(grid)} chars, expected "
                    f"{self.width}x{self.height}={size}"
                )
        unknown = set(self.terrain) - TERRAIN_CHARS.keys()
        if unknown:
            raise ValueError(f"unknown terrain chars {sorted(unknown)}")
        unknown = set(self.roads) - {ROAD_CHAR, NO_ROAD_CHAR}
        if unknown:
            raise ValueError(f"unknown road chars {sorted(unknown)}")
        return self


def encode_map(game_map: GameMap) -> CompactMap:
    """Encode ``game_map`` as grids; raise `MapCodecError` if it is not one.

    The hexes must cover the ``width × height`` offset rectangle exactly once
    each, in row-major order, so that `decode_map` gives back an equal map.
    """
    width = game_map.width
    if len(game_map.hexes) != width * game_map.height:
        raise MapCodecError(
            f"{len(game_map.hexes)} hexes for a {width}x{game_map.height} map"
        )
    terrain: list[str] = []
    roads: list[str] = []
    for index, hex_ in enumerate(game_map.hexes):
        col, row = axial_to_offset(hex_.coord)
        if row * width + col != index or not 0 <= col < width:
            raise MapCodecError(
                f"hex {index} at {hex_.coord} is not cell ({index % width}, "
                f"{index // width}) of a row-major offset rectangle"
            )
        terrain.append(TERRAIN_TO_CHAR[hex_.terrain])
        roads.append(ROAD_CHAR if hex_.road else NO_ROAD_CHAR)
    return CompactMap(
        width=width,
        height=game_map.height,
        terrain="".join(terrain),
        roads="".join(roads),
    )


def decode_map(compact: CompactMap) -> GameMap:
    """Rebuild the `GameMap` that ``compact`` encodes (~2 s for a 349² world, so callers cache it)."""
    width = compact.width
    hexes: list[Hex] = []
    for index, (terrain_char, road_char) in enumerate(
        zip(compact.terrain, compact.roads, strict=True)
    ):
        col, row = index % width, index // width
        hexes.append(
            Hex(
                coord=offset_to_axial(col, row),
                terrain=TERRAIN_CHARS[terrain_char],
                road=road_char == ROAD_CHAR,
            )
        )
    return GameMap(width=width, height=compact.height, hexes=hexes)
