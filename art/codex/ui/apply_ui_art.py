"""Build the shipped UI art from the ratified Codex candidates (ratified 2026-11-11).

Writes every shipped file as a deterministic derivative of a file in this
directory, then records provenance in `applied.json` (read by
`scripts/tests/test_ui_art_sync.py`) and `APPLIED.md` (the same, for people):
source, source sha256, what was done, output, output sha256.

- Glyphs: the picked cut (`glyphs/cut/<slot>-<take>.png`, 256 px, white RGB with
  ink coverage in alpha) resampled to 48 px with Lanczos and saved as an 8-bit
  grey+alpha PNG. The frontend draws them as CSS masks filled with currentColor
  (`frontend/src/components/Glyph.tsx`), which is what `uikit.mask_at` + `stamp`
  did for the review mocks. 48 px covers 16 to 24 CSS px at 2x. Files are named by
  the game's own keys (`oil_field`, `steel_mill`), not the slot names.
- Emblem 1 (`s1-winghex`, header and cards): cropped to its alpha box plus a 4 px
  margin, resized to 240 px wide, WebP quality 90 (alpha lossless).
- Emblem 3 (`s2-compactwing`, favicon): cropped the same way, centred on a
  transparent square, resized to 16, 32 and 48 px for `favicon.ico`, 32 px for
  `favicon-32.png`, and drawn 150 px wide on a wood-900 (#321f14) 180 px square for
  `apple-touch-icon.png` (iOS fills transparency with black).
- Key art (`keyart/raw/centre-3.png`, 1536x1024): kept at its native size (the
  largest Codex returned; upscaling adds bytes, not detail), WebP quality 80,
  method 6.

Run from the repo root: `python3 art/codex/ui/apply_ui_art.py`.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FRONT = ROOT / "frontend"
GLYPH_OUT = FRONT / "src/ui-art/glyphs"
EMBLEM_OUT = FRONT / "src/ui-art/emblem"
KEYART_OUT = FRONT / "src/ui-art/keyart"
PUBLIC = FRONT / "public"

GLYPH_PX = 48
# Shipped name -> picked cut (picks.json, first choice for every slot).
GLYPHS = {
    "manpower": "manpower-A",
    "fuel": "fuel-A",
    "steel": "steel-A",
    "barracks": "barracks-A",
    "depot": "depot-A",
    "workshop": "workshop-A",
    "infantry": "infantry-D",
    "armor": "armor-A",
    "artillery": "artillery-C",
    "army": "army-C",
    "turn": "turn-A",
    "column": "column-A",
    "garrison": "garrison-D",
    "base": "base-A",
    "city": "city-A",
    "farm": "farm-A",
    "oil_field": "oilfield-A",
    "steel_mill": "steelmill-C",
    "factory": "factory-A",
    "travel": "travel-A",
    "deploy": "deploy-A",
    "disband": "disband-A",
}
WOOD_900 = (0x32, 0x1F, 0x14, 255)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def encode(im: Image.Image, fmt: str, **opts: object) -> bytes:
    buf = io.BytesIO()
    im.save(buf, fmt, **opts)
    return buf.getvalue()


def cropped(path: Path, margin: int = 4) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    box = im.getchannel("A").getbbox()
    if box is None:
        raise ValueError(f"{path} is fully transparent")
    x0, y0, x1, y1 = box
    return im.crop((max(0, x0 - margin), max(0, y0 - margin), min(im.width, x1 + margin), min(im.height, y1 + margin)))


def squared(im: Image.Image) -> Image.Image:
    side = max(im.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.alpha_composite(im, ((side - im.width) // 2, (side - im.height) // 2))
    return out


def glyph(cut: Path) -> bytes:
    alpha = Image.open(cut).getchannel("A").resize((GLYPH_PX, GLYPH_PX), Image.LANCZOS)
    white = Image.new("L", alpha.size, 255)
    return encode(Image.merge("LA", (white, alpha)), "PNG", optimize=True)


def emblem_header(src: Path) -> bytes:
    im = cropped(src)
    w = 240
    im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    return encode(im, "WEBP", quality=90, method=6)


def favicon_ico(src: Path) -> bytes:
    sq = squared(cropped(src))
    return encode(sq.resize((48, 48), Image.LANCZOS), "ICO", sizes=[(16, 16), (32, 32), (48, 48)])


def favicon_png(src: Path) -> bytes:
    return encode(squared(cropped(src)).resize((32, 32), Image.LANCZOS), "PNG", optimize=True)


def apple_touch(src: Path) -> bytes:
    im = cropped(src)
    w = 150
    im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
    out = Image.new("RGBA", (180, 180), WOOD_900)
    out.alpha_composite(im, ((180 - im.width) // 2, (180 - im.height) // 2))
    return encode(out.convert("RGB"), "PNG", optimize=True)


def keyart(src: Path) -> bytes:
    return encode(Image.open(src).convert("RGB"), "WEBP", quality=80, method=6)


def main() -> int:
    records: list[dict[str, str]] = []

    def ship(out: Path, src: Path, how: str, data: bytes) -> None:
        write(out, data)
        records.append({"output": rel(out), "output_sha256": sha256(out), "source": rel(src), "source_sha256": sha256(src), "how": how})

    for name, cut in GLYPHS.items():
        src = HERE / "glyphs/cut" / f"{cut}.png"
        ship(GLYPH_OUT / f"{name}.png", src, f"alpha, Lanczos to {GLYPH_PX} px, grey+alpha PNG", glyph(src))

    winghex = HERE / "emblem/cut/s1-winghex.png"
    ship(EMBLEM_OUT / "emblem-1.webp", winghex, "crop to alpha box + 4 px, Lanczos to 240 px wide, WebP q90", emblem_header(winghex))
    compact = HERE / "emblem/cut/s2-compactwing.png"
    ship(PUBLIC / "favicon.ico", compact, "crop + 4 px, centred square, Lanczos 48 px, ICO 16/32/48", favicon_ico(compact))
    ship(PUBLIC / "favicon-32.png", compact, "crop + 4 px, centred square, Lanczos to 32 px PNG", favicon_png(compact))
    ship(PUBLIC / "apple-touch-icon.png", compact, "crop + 4 px, Lanczos to 150 px wide on #321f14 180 px square", apple_touch(compact))

    centre3 = HERE / "keyart/raw/centre-3.png"
    ship(KEYART_OUT / "centre-3.webp", centre3, "native 1536x1024, WebP q80 method 6", keyart(centre3))

    (HERE / "applied.json").write_text(json.dumps(records, indent=1) + "\n")
    lines = [
        "# UI art applied to the frontend",
        "",
        "Generated by `apply_ui_art.py` (do not edit by hand); the picks were ratified",
        "2026-11-11 (see README.md). Each shipped file is a deterministic derivative of",
        "the source named beside it. `scripts/tests/test_ui_art_sync.py` checks both",
        "hashes against `applied.json`.",
        "",
        "| Output | Source | How | Output sha256 | Source sha256 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in records:
        lines.append(
            f"| `{r['output']}` | `{r['source']}` | {r['how']} | `{r['output_sha256'][:16]}` | `{r['source_sha256'][:16]}` |"
        )
    (HERE / "APPLIED.md").write_text("\n".join(lines) + "\n")
    total = sum((ROOT / r["output"]).stat().st_size for r in records)
    print(f"wrote {len(records)} files, {total} bytes")
    for r in records:
        print(f"{(ROOT / r['output']).stat().st_size:>8}  {r['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
