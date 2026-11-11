"""Markers drawn over the map: pins and flags, unit counters, garrison marks,
fight bursts and printed site symbols. Sizes are screen pixels; positions world.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .raster import Ink, Sprites

FONTS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "theme" / "fonts"

RGBA = tuple[int, int, int, int]


def rgba(color: int, alpha: float = 1.0) -> RGBA:
    return (
        (color >> 16) & 0xFF,
        (color >> 8) & 0xFF,
        color & 0xFF,
        int(round(alpha * 255)),
    )


def mix(a: int, b: int, t: float) -> int:
    """Blend two ``0xRRGGBB`` colours."""
    out = 0
    for shift in (16, 8, 0):
        ca, cb = (a >> shift) & 0xFF, (b >> shift) & 0xFF
        out |= int(round(ca + (cb - ca) * t)) << shift
    return out


@lru_cache(maxsize=32)
def font(size_px: int) -> ImageFont.FreeTypeFont:
    """Archivo Narrow Bold (the UI's label face) at ``size_px``."""
    f = ImageFont.truetype(str(FONTS / "ArchivoNarrow-Variable.ttf"), size_px)
    f.set_variation_by_name("Bold")
    return f


# --- pins and flags ---------------------------------------------------------------------


def pin(
    layer: Sprites,
    wx: float,
    wy: float,
    head_px: float,
    color: int,
    outline: int,
    offset: tuple[float, float] = (0.0, 0.0),
    shadow_alpha: float = 0.35,
) -> None:
    """A map pin: needle from the site to a round glossy head, with a soft shadow.

    ``offset`` moves the head (screen px) so the printed symbol under it shows.
    """
    s = layer.ss
    x, y = layer.xy(wx, wy)
    hx, hy = x + offset[0] * s, y + offset[1] * s
    r = head_px * s
    d = layer.draw
    if offset != (0.0, 0.0):
        d.line([(x, y), (hx, hy)], fill=rgba(outline, 0.9), width=max(1, int(0.8 * s)))
    sh = max(1.0, 0.45 * head_px) * s
    d.ellipse(
        (hx - r + sh, hy - r + sh, hx + r + sh, hy + r + sh),
        fill=(0, 0, 0, int(255 * shadow_alpha)),
    )
    d.ellipse(
        (hx - r, hy - r, hx + r, hy + r),
        fill=rgba(color),
        outline=rgba(outline),
        width=max(1, int(round(0.18 * head_px * s))),
    )
    if head_px >= 2.2:
        g = 0.32 * r
        cx, cy = hx - 0.35 * r, hy - 0.35 * r
        d.ellipse((cx - g, cy - g, cx + g, cy + g), fill=(255, 255, 255, 150))


def flag(
    layer: Sprites,
    wx: float,
    wy: float,
    size_px: float,
    color: int,
    outline: int,
) -> None:
    """A base: a pennant on a pole planted at the site."""
    s = layer.ss
    x, y = layer.xy(wx, wy)
    h = size_px * s
    d = layer.draw
    w = max(1, int(round(0.14 * size_px * s)))
    d.ellipse((x - 2 * w, y - w, x + 2 * w, y + w), fill=(0, 0, 0, 90))
    d.line([(x, y), (x, y - h)], fill=rgba(outline), width=w)
    pennant = [
        (x + w / 2, y - h),
        (x + 0.95 * h, y - 0.78 * h),
        (x + w / 2, y - 0.52 * h),
    ]
    d.polygon(pennant, fill=rgba(color), outline=rgba(outline))
    d.line(pennant + [pennant[0]], fill=rgba(outline), width=max(1, w // 2))


def token(
    layer: Sprites,
    wx: float,
    wy: float,
    radius_px: float,
    color: int,
    outline: int,
    outline_px: float = 1.0,
    alpha: float = 1.0,
    ring: int | None = None,
) -> None:
    """A flat round marker (the game's site dot)."""
    s = layer.ss
    x, y = layer.xy(wx, wy)
    r = radius_px * s
    if ring is not None:
        rr = r + 1.4 * s
        layer.draw.ellipse((x - rr, y - rr, x + rr, y + rr), fill=rgba(ring, alpha))
    layer.draw.ellipse(
        (x - r, y - r, x + r, y + r),
        fill=rgba(color, alpha),
        outline=rgba(outline, alpha) if outline_px > 0 else None,
        width=max(1, int(round(outline_px * s))) if outline_px > 0 else 0,
    )


# --- unit counters -----------------------------------------------------------------------


def nato_symbol(
    layer: Sprites,
    cx: float,
    cy: float,
    w: float,
    h: float,
    kind: str,
    color: RGBA,
    line: float,
) -> None:
    """NATO-style type mark inside a box of ``w`` x ``h`` (layer px, centred)."""
    d = layer.draw
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    lw = max(1, int(round(line)))
    if kind == "infantry":
        d.line([(x0, y0), (x1, y1)], fill=color, width=lw)
        d.line([(x0, y1), (x1, y0)], fill=color, width=lw)
    elif kind == "armor":
        ex, ey = 0.36 * w, 0.3 * h
        d.ellipse((cx - ex, cy - ey, cx + ex, cy + ey), outline=color, width=lw)
    else:  # artillery
        r = 0.2 * h + 0.5 * line
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)


def counter(
    layer: Sprites,
    wx: float,
    wy: float,
    width_px: float,
    color: int,
    ink: int,
    face: int,
    kind: str,
    count: int,
    column: bool,
    detail: str,
    rounded: bool = False,
) -> None:
    """A unit counter: a coloured block with a NATO type box and stack count.

    ``detail`` is ``"block"`` (just the coloured block), ``"symbol"`` (block with
    a small type mark) or ``"full"`` (type box plus stack count).
    """
    s = layer.ss
    x, y = layer.xy(wx, wy)
    w = width_px * s
    h = 0.72 * w
    d = layer.draw
    x0, y0 = x - w / 2, y - h / 2
    box = (x0, y0, x0 + w, y0 + h)
    sh = max(1.0, 0.09 * width_px) * s
    shadow = (box[0] + sh, box[1] + sh, box[2] + sh, box[3] + sh)
    radius = 0.18 * h if rounded else 0.06 * h
    d.rounded_rectangle(shadow, radius=radius, fill=(0, 0, 0, 80))
    lw = max(1, int(round(max(0.6, 0.07 * width_px) * s)))
    d.rounded_rectangle(
        box, radius=radius, fill=rgba(color), outline=rgba(ink), width=lw
    )
    if detail == "block":
        return
    if detail == "symbol":
        nato_symbol(layer, x, y, 0.56 * w, 0.5 * h, kind, rgba(face), 0.08 * w)
        return
    # full: a light NATO box on the left, the stack count on the right
    bw, bh = 0.5 * w, 0.56 * h
    bx = x0 + 0.08 * w + bw / 2
    d.rectangle(
        (bx - bw / 2, y - bh / 2, bx + bw / 2, y + bh / 2),
        fill=rgba(face),
        outline=rgba(ink),
        width=max(1, lw // 2),
    )
    nato_symbol(layer, bx, y, bw, bh, kind, rgba(ink), 0.06 * w)
    tx = x0 + 0.08 * w + bw + (w - 0.08 * w - bw) / 2
    if count > 1:
        f = font(max(6, int(round(0.62 * h))))
        d.text((tx, y), str(count), font=f, fill=rgba(face), anchor="mm")
    if column:
        cw = 0.16 * w
        cx = x0 + w + 0.12 * w
        d.line(
            [(cx - cw / 2, y - 0.3 * h), (cx + cw / 2, y), (cx - cw / 2, y + 0.3 * h)],
            fill=rgba(ink),
            width=lw,
        )


def garrison_mark(
    layer: Sprites,
    wx: float,
    wy: float,
    size_px: float,
    count: int,
    color: int,
    ink: int,
    dx: float,
    dy: float,
) -> None:
    """A neutral garrison: a small grey tab with one pip per defender."""
    s = layer.ss
    x, y = layer.xy(wx, wy)
    x += dx * s
    y += dy * s
    n = min(count, 4)
    pip = size_px * 0.32 * s
    w = (n * 2 + 1) * pip
    h = 3 * pip
    d = layer.draw
    d.rectangle(
        (x - w / 2, y - h / 2, x + w / 2, y + h / 2),
        fill=rgba(color),
        outline=rgba(ink, 0.8),
        width=max(1, int(0.5 * s)),
    )
    for k in range(n):
        px = x - w / 2 + pip * (1.5 + 2 * k)
        d.ellipse(
            (px - pip / 2, y - pip / 2, px + pip / 2, y + pip / 2), fill=rgba(0xF2E9D6)
        )


def burst(
    layer: Sprites, wx: float, wy: float, radius_px: float, color: int, ink: int
) -> None:
    """A fight marker: an eight-point starburst."""
    s = layer.ss
    x, y = layer.xy(wx, wy)
    pts = []
    for k in range(16):
        a = math.pi * k / 8 + 0.2
        r = radius_px * s * (1.0 if k % 2 == 0 else 0.45)
        pts.append((x + r * math.cos(a), y + r * math.sin(a)))
    layer.draw.polygon(
        pts, fill=rgba(color), outline=rgba(ink), width=max(1, int(0.5 * s))
    )


# --- printed symbols (ink masks) --------------------------------------------------------------


def site_symbol(
    ink: Ink, wx: float, wy: float, kind: str, size_px: float, level: int
) -> None:
    """A conventional-sign site symbol in ink, ``size_px`` across; level widens it."""
    s = size_px * (0.85 + 0.15 * level)
    lw = max(0.7, 0.11 * s)
    if kind == "farm":
        h = 0.45 * s
        ink.line_px(wx, wy, [(-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)], lw)
        ink.circle(wx, wy, 0.16 * s)
    elif kind == "oil_field":
        ink.line_px(
            wx, wy, [(-0.42 * s, 0.5 * s), (0, -0.5 * s), (0.42 * s, 0.5 * s)], lw
        )
        ink.line_px(wx, wy, [(-0.22 * s, 0.05 * s), (0.22 * s, 0.05 * s)], lw)
        ink.circle(wx, wy, 0.14 * s, dy=-0.5 * s)
    elif kind == "steel_mill":
        ink.polygon_px(
            wx,
            wy,
            [
                (-0.5 * s, 0.45 * s),
                (0.5 * s, 0.45 * s),
                (0.5 * s, -0.05 * s),
                (-0.5 * s, -0.05 * s),
            ],
        )
        ink.polygon_px(
            wx,
            wy,
            [
                (0.18 * s, -0.05 * s),
                (0.36 * s, -0.05 * s),
                (0.36 * s, -0.6 * s),
                (0.18 * s, -0.6 * s),
            ],
        )
    elif kind == "factory":
        ink.polygon_px(
            wx,
            wy,
            [
                (-0.55 * s, 0.4 * s),
                (0.55 * s, 0.4 * s),
                (0.55 * s, -0.35 * s),
                (0.2 * s, -0.05 * s),
                (0.2 * s, -0.35 * s),
                (-0.15 * s, -0.05 * s),
                (-0.15 * s, -0.35 * s),
                (-0.55 * s, -0.05 * s),
            ],
        )
