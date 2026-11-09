"""PNG debug render of a generated world (WORLD_SPEC §6 "Review").

A map-design aid, not board art: each hex is a small block of pixels in its
flat-top odd-q position, tinted by start region, with rivers, roads, province
borders, settlements and capitals on top. Written with the standard library
(zlib + struct) so previews need no imaging dependency.

Run: ``uv run python -m app.world.preview --slots 200 --seed 1 --out world.png``.
"""

from __future__ import annotations

import argparse
import colorsys
import struct
import zlib
from pathlib import Path

from app.engine.stats import Terrain
from app.world.generate import GeneratedWorld, WorldParams, generate

_Rgb = tuple[int, int, int]

_TERRAIN_SHADE: dict[Terrain, tuple[float, float, float]] = {
    Terrain.PLAINS: (1.0, 1.0, 1.0),
    Terrain.WOODS: (0.62, 0.80, 0.60),
    Terrain.HILLS: (0.92, 0.80, 0.62),
}
_RIVER: _Rgb = (40, 95, 215)
_BRIDGE: _Rgb = (150, 115, 70)
_ROAD: _Rgb = (55, 50, 45)
_UNCLAIMED: _Rgb = (214, 214, 214)
_TOWN: _Rgb = (255, 255, 255)
_CITY: _Rgb = (235, 60, 40)
_CAPITAL: _Rgb = (10, 10, 10)
_PROVINCE_BORDER = 0.92  # brightness factor on province edges
_TERRITORY_BORDER = 0.40  # stronger darkening where start regions meet


def region_palette(count: int) -> list[_Rgb]:
    """Distinct pale tints per start region, skipping the blues rivers use."""
    colors: list[_Rgb] = []
    for k in range(count):
        hue = (k * 0.61803398875) % 1.0
        hue = (hue * 0.82 + (0.15 if hue * 0.82 > 0.50 else 0.0)) % 1.0
        sat = 0.30 + 0.10 * ((k * 7) % 3) / 2
        val = 0.86 + 0.06 * ((k * 5) % 3) / 2
        r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
        colors.append((int(255 * r), int(255 * g), int(255 * b)))
    return colors


def render(world: GeneratedWorld, scale: int = 3) -> bytes:
    """Encode the world as a PNG, ``scale`` pixels per hex."""
    width = world.game_map.width
    height = world.game_map.height
    pixel_w = width * scale
    pixel_h = height * scale + scale // 2 + 1
    canvas = [[(255, 255, 255)] * pixel_w for _ in range(pixel_h)]
    palette = region_palette(world.params.slots)
    hexes = world.game_map.hexes

    def index(col: int, row: int) -> int:
        return row * width + col

    for i, hex_ in enumerate(hexes):
        col, row = i % width, i // width
        color = _hex_color(hex_.terrain, world.territory[i], palette)
        factor = 1.0
        for ncol, nrow in _offset_neighbors(col, row):
            if not (0 <= ncol < width and 0 <= nrow < height):
                continue
            j = index(ncol, nrow)
            if (
                world.territory[j] != world.territory[i]
                and min(world.territory[i], world.territory[j]) >= 0
            ):
                factor = min(factor, _TERRITORY_BORDER)
            elif world.province[j] != world.province[i]:
                factor = min(factor, _PROVINCE_BORDER)
        if hex_.terrain not in (Terrain.RIVER, Terrain.BRIDGE):
            color = _shade(color, (factor, factor, factor))
        _block(canvas, col, row, scale, color)

    for i, hex_ in enumerate(hexes):
        if not hex_.road:
            continue
        col, row = i % width, i // width
        _dot(canvas, col, row, scale, _ROAD, 0)
        for ncol, nrow in _offset_neighbors(col, row):
            if (
                0 <= ncol < width
                and 0 <= nrow < height
                and hexes[index(ncol, nrow)].road
            ):
                _line(
                    canvas, _center(col, row, scale), _center(ncol, nrow, scale), _ROAD
                )

    capitals = set(world.capitals)
    for i, hex_ in enumerate(hexes):
        col, row = i % width, i // width
        if hex_.coord in capitals:
            _dot(canvas, col, row, scale, _CAPITAL, 2)
            _dot(canvas, col, row, scale, _CITY, 1)
        elif hex_.terrain is Terrain.CITY:
            _dot(canvas, col, row, scale, _CITY, 1)
        elif hex_.terrain is Terrain.TOWN:
            _dot(canvas, col, row, scale, _TOWN, 1)
    return _encode_png(canvas)


def _hex_color(terrain: Terrain, territory: int, palette: list[_Rgb]) -> _Rgb:
    if terrain is Terrain.RIVER:
        return _RIVER
    if terrain is Terrain.BRIDGE:
        return _BRIDGE
    base = palette[territory] if territory >= 0 else _UNCLAIMED
    return _shade(base, _TERRAIN_SHADE.get(terrain, (1.0, 1.0, 1.0)))


def _shade(color: _Rgb, factor: tuple[float, float, float]) -> _Rgb:
    return (
        int(color[0] * factor[0]),
        int(color[1] * factor[1]),
        int(color[2] * factor[2]),
    )


def _offset_neighbors(col: int, row: int) -> list[tuple[int, int]]:
    """Right and down neighbours in odd-q offset (enough to find every border once)."""
    if col & 1:
        return [(col + 1, row), (col + 1, row + 1), (col, row + 1)]
    return [(col + 1, row - 1), (col + 1, row), (col, row + 1)]


def _origin(col: int, row: int, scale: int) -> tuple[int, int]:
    return col * scale, row * scale + (scale // 2 if col & 1 else 0)


def _block(
    canvas: list[list[_Rgb]], col: int, row: int, scale: int, color: _Rgb
) -> None:
    x0, y0 = _origin(col, row, scale)
    for y in range(y0, y0 + scale):
        line = canvas[y]
        for x in range(x0, x0 + scale):
            line[x] = color


def _center(col: int, row: int, scale: int) -> tuple[int, int]:
    x0, y0 = _origin(col, row, scale)
    return x0 + scale // 2, y0 + scale // 2


def _line(
    canvas: list[list[_Rgb]], a: tuple[int, int], b: tuple[int, int], color: _Rgb
) -> None:
    """Plot a 1-pixel line from ``a`` to ``b`` (simple DDA)."""
    steps = max(abs(b[0] - a[0]), abs(b[1] - a[1]), 1)
    for k in range(steps + 1):
        x = a[0] + (b[0] - a[0]) * k // steps
        y = a[1] + (b[1] - a[1]) * k // steps
        canvas[y][x] = color


def _dot(
    canvas: list[list[_Rgb]], col: int, row: int, scale: int, color: _Rgb, radius: int
) -> None:
    x0, y0 = _origin(col, row, scale)
    cx, cy = x0 + scale // 2, y0 + scale // 2
    for y in range(max(0, cy - radius), min(len(canvas), cy + radius + 1)):
        line = canvas[y]
        for x in range(max(0, cx - radius), min(len(line), cx + radius + 1)):
            line[x] = color


def _encode_png(canvas: list[list[_Rgb]]) -> bytes:
    """Minimal truecolor PNG encoder (filter 0 on every row)."""
    height = len(canvas)
    width = len(canvas[0])
    raw = bytearray()
    for line in canvas:
        raw.append(0)
        for r, g, b in line:
            raw += bytes((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    """CLI: generate a world and write its preview PNG."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", type=int, default=12)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--scale", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    world = generate(WorldParams.for_slots(args.slots, args.seed))
    args.out.write_bytes(render(world, args.scale))
    for key, value in world.stats.items():
        print(f"{key:30s} {value}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
