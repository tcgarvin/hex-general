"""Build the sites round 4 oil/factory count-ladder review images.

Writes into art/codex/terrain/review/sites4/web/:
  oil-levels.png, factory-levels.png
      columns L1, L2, L3; one row per variant at 2x (256 px), then the same grid
      at 64 px per hex (game scale)
  oil-before-after.png, factory-before-after.png
      one tile per level: sites3 (top, "before") over sites4 (bottom, "after"), 2x
  oil-factory-board.png, oil-factory-board-128.png
      scripts/board-mock.py renders of scripts/board-mock-maps/sites4-oilfac.txt with
      joins, at 64 and 128 px per hex; the sites dir combines the sites4 oil and
      factory picks with the sites3 farm picks (built under --scratch)
  oil-tanks-before-after.png
      oil L2/L3 picks before the tank repaint (sites/oil_field/s4-tanks-l*/pick_before/)
      over the current picks, 2x, with 64 px versions alongside

usage: python3 art/codex/terrain/sites4_oilfac_review.py --scratch <dir>
       [--before oil_field=0,0,0] [--after oil_field=0,0,0] (variant index per level)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
R3 = HERE / "candidates" / "sites3"
R4 = HERE / "candidates" / "sites4"
OUT = HERE / "review" / "sites4" / "web"
BOARD_MAP = REPO / "scripts" / "board-mock-maps" / "sites4-oilfac.txt"
JOINS = REPO / "frontend" / "src" / "board" / "joins"

BG = (0x1A, 0x1B, 0x16)
FG = (225, 218, 190)
DIM = (150, 146, 128)
PAD = 12
SHORT = {"oil_field": "oil", "factory": "factory"}
KEY = {"oil_field": "derricks", "factory": "sawtooth halls"}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


F = font(15)
FS = font(12)


def tile(path: Path, px: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    return im if px == im.width else im.resize((px, px), Image.Resampling.LANCZOS)


def by_level(root: Path, kind: str) -> dict[int, list[Path]]:
    levels = {lv: sorted((root / kind).glob(f"l{lv}_*.png")) for lv in (1, 2, 3)}
    for lv, paths in levels.items():
        if not paths:
            raise SystemExit(f"{root / kind}: no level {lv} tiles")
    return levels


def canvas(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGBA", (w, h), BG + (255,))
    return im, ImageDraw.Draw(im)


def grid(
    im: Image.Image,
    d: ImageDraw.ImageDraw,
    levels: dict[int, list[Path]],
    px: int,
    x0: int,
    y0: int,
    gap: int,
) -> int:
    """Draw level columns x variant rows at ``px``; return the bottom y."""
    rows = max(len(v) for v in levels.values())
    for c, lv in enumerate((1, 2, 3)):
        x = x0 + c * (px + gap)
        d.text((x + px // 2 - 12, y0), f"L{lv}", font=FS if px < 128 else F, fill=FG)
        for r, p in enumerate(levels[lv]):
            im.alpha_composite(tile(p, px), (x, y0 + 20 + r * (px + gap // 2)))
    return y0 + 20 + rows * (px + gap // 2)


def levels_sheet(kind: str) -> Image.Image:
    levels = by_level(R4, kind)
    rows = max(len(v) for v in levels.values())
    lab = 36
    w = lab + 3 * (256 + PAD) + PAD
    h = 34 + 20 + rows * (256 + PAD // 2) + 40 + 20 + rows * (64 + PAD // 2) + PAD
    im, d = canvas(w, h)
    d.text(
        (PAD, 8),
        f"{SHORT[kind]}: sites4 picks, level N = N {KEY[kind]}; rows = variants",
        font=F,
        fill=FG,
    )
    y = grid(im, d, levels, 256, lab, 34, PAD)
    for r in range(rows):
        d.text((PAD, 34 + 20 + r * (256 + PAD // 2) + 120), f"v{r}", font=FS, fill=DIM)
    y += 12
    d.text((PAD, y), "same grid at 64 px per hex (game scale)", font=FS, fill=DIM)
    grid(im, d, levels, 64, lab, y + 18, PAD)
    return im


def before_after(kind: str, before: list[int], after: list[int]) -> Image.Image:
    b3, b4 = by_level(R3, kind), by_level(R4, kind)
    lab = 70
    w = lab + 3 * (256 + PAD) + PAD
    h = 34 + 20 + 2 * (256 + PAD) + PAD
    im, d = canvas(w, h)
    d.text((PAD, 8), f"{SHORT[kind]}: sites3 (before) vs sites4 (after), 2x", font=F, fill=FG)
    for c, lv in enumerate((1, 2, 3)):
        x = lab + c * (256 + PAD)
        d.text((x + 116, 34), f"L{lv}", font=F, fill=FG)
        im.alpha_composite(tile(b3[lv][before[c]], 256), (x, 54))
        im.alpha_composite(tile(b4[lv][after[c]], 256), (x, 54 + 256 + PAD))
    d.text((PAD, 54 + 120), "before", font=F, fill=FG)
    d.text((PAD, 54 + 256 + PAD + 120), "after", font=F, fill=FG)
    return im


def tanks_before_after() -> Image.Image:
    """Previous oil L2/L3 picks (pre tank repaint, kept in the s4-tanks-l* work dirs)
    over the current ones, 2x, with 64 px versions alongside."""
    work = HERE / "sites" / "oil_field"
    before = sorted((work / "s4-tanks-l2" / "pick_before").glob("l2_*.png")) + sorted(
        (work / "s4-tanks-l3" / "pick_before").glob("l3_*.png")
    )
    after = [R4 / "oil_field" / p.name for p in before]
    lab, cell = 70, 256 + 6
    w = lab + len(before) * cell + 24 + len(before) * 70 + PAD
    h = 34 + 20 + 2 * (256 + PAD) + PAD
    im, d = canvas(w, h)
    d.text((PAD, 8), "oil tanks: previous L2/L3 picks (before) vs repainted tanks (after)", font=F, fill=FG)
    x64 = lab + len(before) * cell + 24
    d.text((x64, 34), "64 px", font=FS, fill=DIM)
    for i, (b, a) in enumerate(zip(before, after)):
        d.text((lab + i * cell + 110, 34), b.stem, font=FS, fill=FG)
        for r, p in enumerate((b, a)):
            y = 54 + r * (256 + PAD)
            im.alpha_composite(tile(p, 256), (lab + i * cell, y))
            im.alpha_composite(tile(p, 64), (x64 + i * 70, y + 96))
    d.text((PAD, 54 + 120), "before", font=F, fill=FG)
    d.text((PAD, 54 + 256 + PAD + 120), "after", font=F, fill=FG)
    return im


def board(scratch: Path) -> None:
    sites = scratch / "board-sites"
    if sites.exists():
        shutil.rmtree(sites)
    for kind, root in (("oil_field", R4), ("factory", R4), ("farm", R3)):
        (sites / kind).mkdir(parents=True)
        for p in (root / kind).glob("l*_*.png"):
            shutil.copyfile(p, sites / kind / p.name)
    for px, name in ((64, "oil-factory-board.png"), (128, "oil-factory-board-128.png")):
        subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts" / "board-mock.py"),
                "--map",
                str(BOARD_MAP),
                "--joins",
                str(JOINS),
                "--sites",
                str(sites),
                "--hex-px",
                str(px),
                "--out",
                str(OUT / name),
            ],
            check=True,
        )


def parse_pick(specs: list[str]) -> dict[str, list[int]]:
    out = {"oil_field": [0, 0, 0], "factory": [0, 0, 0]}
    for spec in specs:
        kind, idx = spec.split("=")
        out[kind] = [int(i) for i in idx.split(",")]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--before", action="append", default=[])
    ap.add_argument("--after", action="append", default=[])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    before, after = parse_pick(args.before), parse_pick(args.after)
    for kind in ("oil_field", "factory"):
        levels_sheet(kind).convert("RGB").save(OUT / f"{SHORT[kind]}-levels.png", optimize=True)
        before_after(kind, before[kind], after[kind]).convert("RGB").save(
            OUT / f"{SHORT[kind]}-before-after.png", optimize=True
        )
    if (HERE / "sites" / "oil_field" / "s4-tanks-l2" / "pick_before").is_dir():
        tanks_before_after().convert("RGB").save(OUT / "oil-tanks-before-after.png", optimize=True)
    board(args.scratch)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
