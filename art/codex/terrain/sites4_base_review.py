"""Build the sites round 4 base review images (perimeter bank at every stage).

Writes web-ready PNGs into art/codex/terrain/review/sites4/web/ (background #1a1b16):
  base-growth-full.png        stages 1-4 (variant a), "full" bank at stages 1-2; 128 px, 64 px row below
  base-growth-building.png    the same with the "building" bank at stages 1-2
  base-wall-options.png       stages 1 and 2 at 2x: before (sites3, no bank) | full | building
  base-variants.png           every pick, grouped by stage and flavour, 128 px per hex
  base-board-full.png         scripts/board-mock.py render of sites4-base.txt at 64 px with joins,
  base-board-building.png     one per flavour (temporary sites dirs under --scratch)
  base-board-full-128.png     --hex-px 128 crop of the west half of each board
  base-board-building-128.png

usage: python3 art/codex/terrain/sites4_base_review.py --scratch <dir>
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from hexgeom import axial_to_pixel  # noqa: E402

PICKS = HERE / "candidates" / "sites4" / "base"
BEFORE = HERE / "candidates" / "sites3" / "base"
OUT = HERE / "review" / "sites4" / "web"
MAP = REPO / "scripts" / "board-mock-maps" / "sites4-base.txt"
BG = (0x1A, 0x1B, 0x16)
FG = (232, 222, 192)
DIM = (160, 152, 128)
PAD = 12
BOARD_CROP_128 = "0,0,6,9"  # west half: stage 3, both stage 2s, the town and a stage 1


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F = font(15)
FS = font(12)


def assemble(tiles_dir: Path, hex_px: int, pad: int = 4) -> Image.Image:
    """A cluster's member tiles in position on a transparent canvas."""
    pos = json.loads((tiles_dir / "positions.json").read_text())["positions"]
    pts = {n: axial_to_pixel(q, r, hex_px / 2) for n, (q, r) in pos.items()}
    x0 = min(p[0] for p in pts.values()) - hex_px / 2 - pad
    y0 = min(p[1] for p in pts.values()) - hex_px / 2 - pad
    x1 = max(p[0] for p in pts.values()) + hex_px / 2 + pad
    y1 = max(p[1] for p in pts.values()) + hex_px / 2 + pad
    canvas = Image.new("RGBA", (math.ceil(x1 - x0), math.ceil(y1 - y0)), (0, 0, 0, 0))
    for n, (x, y) in pts.items():
        t = Image.open(tiles_dir / f"{n}.png").convert("RGBA")
        if t.width != hex_px:
            t = t.resize((hex_px, hex_px), Image.Resampling.LANCZOS)
        canvas.alpha_composite(t, (round(x - x0 - hex_px / 2), round(y - y0 - hex_px / 2)))
    return canvas


def new_canvas(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGBA", (w, h), BG + (255,))
    return im, ImageDraw.Draw(im)


def stage_dir(stage: int, flavour: str, variant: str) -> Path:
    """The pick dir: stages 1-2 carry a flavour, stages 3-4 are full only."""
    name = f"stage{stage}_{flavour}_{variant}" if stage <= 2 else f"stage{stage}_{variant}"
    path = PICKS / name
    if not (path / "positions.json").is_file():
        raise FileNotFoundError(f"missing pick {path}")
    return path


def growth(flavour: str) -> Image.Image:
    dirs = [stage_dir(s, flavour, "a") for s in (1, 2, 3, 4)]
    big = [assemble(d, 128) for d in dirs]
    small = [assemble(d, 64, 2) for d in dirs]
    w = PAD + sum(b.width + PAD for b in big)
    h = 30 + 22 + max(b.height for b in big) + 26 + max(s.height for s in small) + PAD
    im, d = new_canvas(w, h)
    bank = "complete bank" if flavour == "full" else "bank being built"
    d.text((PAD, 8), f"base growth, stages 1-4 (variant a): {bank} at stages 1-2; 128 px per hex, 64 px below", font=F, fill=FG)
    x = PAD
    y_small = 52 + max(b.height for b in big) + 26
    for s, (b, sm) in enumerate(zip(big, small), start=1):
        tag = f"stage {s}" + (f" ({flavour})" if s <= 2 else "")
        d.text((x, 32), tag, font=FS, fill=FG)
        im.alpha_composite(b, (x, 52))
        d.text((x, y_small - 18), f"{tag}, 64 px", font=FS, fill=DIM)
        im.alpha_composite(sm, (x, y_small))
        x += b.width + PAD
    return im


def wall_options() -> Image.Image:
    def before(s: int) -> Path:
        return BEFORE / f"stage{s}_a"

    def full(s: int) -> Path:
        return stage_dir(s, "full", "a")

    def building(s: int) -> Path:
        return stage_dir(s, "building", "a")

    cols: list[tuple[str, Callable[[int], Path]]] = [
        ("before (sites3, no bank)", before), ("full bank", full), ("bank being built", building),
    ]
    cells = {(s, c): assemble(fn(s), 256, 6) for s in (1, 2) for c, (_, fn) in enumerate(cols)}
    col_w = [max(cells[(s, c)].width for s in (1, 2)) for c in range(3)]
    row_h = [max(cells[(s, c)].height for c in range(3)) for s in (1, 2)]
    lab = 70
    w = lab + sum(col_w) + 3 * PAD
    h = 34 + 22 + sum(row_h) + 2 * PAD
    im, d = new_canvas(w, h)
    d.text((PAD, 8), "perimeter options at stages 1 and 2, 2x (256 px per hex), variant a", font=F, fill=FG)
    x = lab
    for c, (name, _) in enumerate(cols):
        d.text((x, 34), name, font=F, fill=FG)
        x += col_w[c] + PAD
    y = 56
    for r, s in enumerate((1, 2)):
        d.text((PAD, y + row_h[r] // 2), f"stage {s}", font=F, fill=FG)
        x = lab
        for c in range(3):
            im.alpha_composite(cells[(s, c)], (x, y))
            x += col_w[c] + PAD
        y += row_h[r] + PAD
    return im


def variants() -> Image.Image:
    picks = {p["dir"]: p for p in json.loads((PICKS / "picks.json").read_text())}
    groups: list[tuple[str, list[str]]] = [
        ("stage 1", ["stage1_full_a", "stage1_full_b", "stage1_building_a", "stage1_building_b"]),
        ("stage 2", ["stage2_full_a", "stage2_full_b", "stage2_building_a", "stage2_building_b"]),
        ("stage 3", ["stage3_a", "stage3_b"]),
        ("stage 4", ["stage4_a", "stage4_b"]),
    ]
    rows = [(g, [(n, assemble(PICKS / n, 128)) for n in names]) for g, names in groups]
    lab = 70
    w = lab + max(sum(i.width + PAD for _, i in r) for _, r in rows) + PAD
    h = 34 + sum(max(i.height for _, i in r) + 36 for _, r in rows)
    im, d = new_canvas(w, h)
    d.text((PAD, 8), "every base pick, 128 px per hex (label: pick dir <- work dir:cluster)", font=F, fill=FG)
    y = 34
    for g, r in rows:
        d.text((PAD, y + 40), g, font=F, fill=FG)
        x = lab
        for name, img in r:
            src = picks[name]["work_cluster"]
            note = " (sites3)" if "reused_from" in picks[name] else ""
            d.text((x, y), name.replace(f"stage{g[-1]}_", ""), font=FS, fill=FG)
            d.text((x, y + 15), f"{src}{note}", font=FS, fill=DIM)
            im.alpha_composite(img, (x, y + 32))
            x += img.width + PAD
        y += max(i.height for _, i in r) + 36
    return im


def board_sites(scratch: Path, flavour: str) -> Path:
    """A temporary --sites dir: base/stage<N>_<v> -> the flavour's picks (copies)."""
    root = scratch / f"board-sites-{flavour}"
    if root.exists():
        shutil.rmtree(root)
    for s in (1, 2, 3, 4):
        for v in ("a", "b"):
            shutil.copytree(stage_dir(s, flavour, v), root / "base" / f"stage{s}_{v}")
    return root


def board(scratch: Path, flavour: str, hex_px: int, out: Path, crop: str | None) -> None:
    cmd = [
        sys.executable, str(REPO / "scripts" / "board-mock.py"), "--map", str(MAP),
        "--joins", str(REPO / "frontend" / "src" / "board" / "joins"),
        "--sites", str(board_sites(scratch, flavour)), "--hex-px", str(hex_px), "--out", str(out),
    ]
    if crop is not None:
        cmd += ["--crop", crop]
    subprocess.run(cmd, check=True, cwd=REPO, stdout=subprocess.DEVNULL)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", type=Path, required=True, help="dir for the temporary board sites dirs")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for flavour in ("full", "building"):
        growth(flavour).convert("RGB").save(OUT / f"base-growth-{flavour}.png", optimize=True)
        board(args.scratch, flavour, 64, OUT / f"base-board-{flavour}.png", None)
        board(args.scratch, flavour, 128, OUT / f"base-board-{flavour}-128.png", BOARD_CROP_128)
    wall_options().convert("RGB").save(OUT / "base-wall-options.png", optimize=True)
    variants().convert("RGB").save(OUT / "base-variants.png", optimize=True)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
