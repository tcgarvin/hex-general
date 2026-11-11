"""Shared helpers for the UI candidate review renders (glyphs, emblem, key art).

Surfaces and type match the live app (`frontend/src/index.css`, UI-STYLE §2-3):
the header wood is `board/panels/chrome/wood.png` tiled under an 18 % ink wash
(`.fhq-wood`), parchment is the flat `parchment-200` token, and the three
ratified families are read straight from the TTFs in `frontend/src/theme/fonts/`
(the repo ships TTF, so no woff2 conversion is needed).
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
WOOD_PNG = os.path.join(REPO, "frontend/src/board/panels/chrome/wood.png")
FONTS = os.path.join(REPO, "frontend/src/theme/fonts")

IVORY = (0xF0, 0xE6, 0xD2)
IVORY_400 = (0xA8, 0x98, 0x80)
INK = (0x2B, 0x21, 0x18)
PARCHMENT = (0xE4, 0xCD, 0xA2)
PARCHMENT_400 = (0xAA, 0x96, 0x75)
BRASS_600 = (0xA1, 0x79, 0x3E)
BRASS_400 = (0xD9, 0xB0, 0x60)
WOOD_900 = (0x32, 0x1F, 0x14)
VALID = (0x7F, 0xA6, 0x5A)
INVALID = (0xC0, 0x3B, 0x2E)
REVIEW_BG = (0x2A, 0x2A, 0x2E)


def wood(w: int, h: int, wash: float = 0.18) -> Image.Image:
    """The `.fhq-wood` surface: wood.png tiled, under an ink wash of `wash` opacity."""
    tile = Image.open(WOOD_PNG).convert("RGB")
    out = Image.new("RGB", (w, h))
    for y in range(0, h, tile.height):
        for x in range(0, w, tile.width):
            out.paste(tile, (x, y))
    return Image.blend(out, Image.new("RGB", (w, h), INK), wash)


def parchment(w: int, h: int) -> Image.Image:
    return Image.new("RGB", (w, h), PARCHMENT)


def mask_at(path: str, size: int) -> Image.Image:
    """A glyph's alpha mask resampled to `size` px (what a browser draws at that size)."""
    return Image.open(path).getchannel("A").resize((size, size), Image.LANCZOS)


def stamp(dest: Image.Image, mask: Image.Image, colour: tuple[int, int, int], xy: tuple[int, int]) -> None:
    """Draw a tinted glyph mask onto an RGB image."""
    layer = Image.new("RGB", mask.size, colour)
    dest.paste(layer, xy, mask)


def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    """role: display (Allerta Stencil), label (Archivo Narrow), data (Special Elite)."""
    files = {
        "display": "AllertaStencil-Regular.ttf",
        "label": "ArchivoNarrow-Variable.ttf",
        "data": "SpecialElite-Regular.ttf",
    }
    f = ImageFont.truetype(os.path.join(FONTS, files[role]), size)
    if role == "label":
        f.set_variation_by_axes([600])
    return f


def label_font(size: int) -> ImageFont.FreeTypeFont:
    """A plain sans for review captions (not part of the mocked UI)."""
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)


def zoom(im: Image.Image, k: int) -> Image.Image:
    """Nearest-neighbour enlarge, so the reviewer sees the real pixels."""
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, f: ImageFont.FreeTypeFont, fill) -> int:
    """Draw text with its left edge at x and its vertical centre at y; return the advance."""
    x, y = xy
    draw.text((x, y), s, font=f, fill=fill, anchor="lm")
    return int(draw.textlength(s, font=f))
