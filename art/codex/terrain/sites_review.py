"""Build the sites1 review images from art/codex/terrain/candidates/sites1.

Writes into art/codex/terrain/review/sites1/:
  <kind>.png          columns = level 1..3, rows = variants at 128 px, a 64 px strip beneath,
                      and the kind's existing emblem (frontend/src/world/sites) beside its L2 tile
  all-kinds-64.png    one tile of every kind at every level at 64 px (base row = stages 1-4)
  emblems-vs-l2.png   every emblem beside its kind's level-2 tile (128 and 64 px)
  base-stages.png     base stages 1-4 left to right at 128 px, 64 px strip beneath
  base-stages.json    per stage: footprint, occupied share over the whole footprint
  measures.json       per single-hex tile: occupied, built, L, gates (from picks.json)

usage: python3 art/codex/terrain/sites_review.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from paintin_cluster import assemble  # noqa: E402
from paintin_geom import tile_distance  # noqa: E402
from paintin_pin import PinConfig, ground_restore  # noqa: E402

CAND = HERE / "candidates" / "sites1"
OUT = HERE / "review" / "sites1"
EMBLEMS = REPO / "frontend" / "src" / "world" / "sites"
KINDS = ("farm", "oil_field", "steel_mill", "factory")
BG = (34, 34, 40)
FG = (235, 225, 190)
PAD = 10


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


F = font(14)
FS = font(11)


def tile(path: Path, px: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    return im if px == im.width else im.resize((px, px), Image.Resampling.LANCZOS)


def by_level(kind: str) -> dict[int, list[Path]]:
    return {lv: sorted((CAND / kind).glob(f"l{lv}_*.png")) for lv in (1, 2, 3)}


def on_bg(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", size, BG + (255,))


def kind_sheet(kind: str) -> Image.Image:
    levels = by_level(kind)
    rows = max(len(v) for v in levels.values())
    head = 24
    col_w = 128 + PAD
    emblem_w = 2 * (128 + PAD) + PAD
    strip_h = 64 + 2 * PAD + 16
    w = 70 + 3 * col_w + emblem_w
    h = head + 18 + rows * (128 + PAD) + strip_h
    im = on_bg((w, h))
    d = ImageDraw.Draw(im)
    d.text((PAD, 4), f"{kind}: columns = level, rows = variants (128 px); 64 px strip below", font=F, fill=FG)
    for c, lv in enumerate((1, 2, 3)):
        d.text((70 + c * col_w + 40, head), f"level {lv}", font=F, fill=FG)
        for r, p in enumerate(levels[lv]):
            im.alpha_composite(tile(p, 128), (70 + c * col_w, head + 18 + r * (128 + PAD)))
    for r in range(rows):
        d.text((PAD, head + 18 + r * (128 + PAD) + 56), f"v{r}", font=F, fill=FG)
    ex = 70 + 3 * col_w + PAD
    d.text((ex, head), "emblem | L2 tile", font=F, fill=FG)
    im.alpha_composite(tile(EMBLEMS / f"{kind}.png", 128), (ex, head + 18))
    im.alpha_composite(tile(levels[2][0], 128), (ex + 128 + PAD, head + 18))
    y = head + 18 + rows * (128 + PAD) + PAD
    d.text((PAD, y), "64 px:", font=FS, fill=FG)
    x = 70
    for lv in (1, 2, 3):
        d.text((x, y), f"L{lv}", font=FS, fill=FG)
        for p in levels[lv]:
            im.alpha_composite(tile(p, 64), (x, y + 14))
            x += 64 + 2
        x += PAD + 6
    ey = head + 18 + 128 + PAD
    d.text((ex, ey), "at 64 px:", font=FS, fill=FG)
    im.alpha_composite(tile(EMBLEMS / f"{kind}.png", 64), (ex, ey + 14))
    im.alpha_composite(tile(levels[2][0], 64), (ex + 64 + 4, ey + 14))
    return im


def stage_dirs() -> dict[int, list[Path]]:
    return {s: sorted((CAND / "base").glob(f"stage{s}_*")) for s in (1, 2, 3, 4)}


def all_kinds_64() -> Image.Image:
    lab = 90
    cell = 64 + 14
    stages = [assemble(stage_dirs()[s][0], hex_px=64, pad=0) for s in (1, 2, 3, 4)]
    base_w = sum(s.width for s in stages) + 4 * 14
    w = max(lab + 3 * cell, lab + base_w) + PAD
    h = 22 + 16 + len(KINDS) * (64 + 10) + max(s.height for s in stages) + 24
    im = on_bg((w, h))
    d = ImageDraw.Draw(im)
    d.text((PAD, 4), "all kinds at 64 px (first variant of each level)", font=F, fill=FG)
    for c, lv in enumerate((1, 2, 3)):
        d.text((lab + c * cell + 20, 22), f"L{lv}", font=FS, fill=FG)
    for r, kind in enumerate(KINDS):
        y = 38 + r * (64 + 10)
        d.text((PAD, y + 24), kind, font=FS, fill=FG)
        for c, lv in enumerate((1, 2, 3)):
            im.alpha_composite(tile(by_level(kind)[lv][0], 64), (lab + c * cell, y))
    y = 38 + len(KINDS) * (64 + 10)
    d.text((PAD, y + 20), "base\nstages 1-4", font=FS, fill=FG)
    x = lab
    for s, st in enumerate(stages, start=1):
        d.text((x, y), f"S{s}", font=FS, fill=FG)
        im.paste(st, (x, y + 14))
        x += st.width + 14
    return im


def emblems_vs_l2() -> Image.Image:
    kinds = KINDS + ("base",)
    w = 100 + 2 * (128 + PAD) + 2 * (64 + PAD) + PAD
    h = 24 + len(kinds) * (128 + PAD)
    im = on_bg((w, h))
    d = ImageDraw.Draw(im)
    d.text((PAD, 4), "emblem | level-2 tile (base: stage 1) at 128 and 64 px", font=F, fill=FG)
    for r, kind in enumerate(kinds):
        y = 24 + r * (128 + PAD)
        l2 = (
            stage_dirs()[1][0] / "h0.png" if kind == "base" else by_level(kind)[2][0]
        )
        d.text((PAD, y + 56), kind, font=FS, fill=FG)
        x = 100
        for p, px in ((EMBLEMS / f"{kind}.png", 128), (l2, 128), (EMBLEMS / f"{kind}.png", 64), (l2, 64)):
            im.alpha_composite(tile(p, px), (x, y + (128 - px) // 2))
            x += px + PAD
    return im


def base_stages() -> Image.Image:
    dirs = stage_dirs()
    rows = max(len(v) for v in dirs.values())
    big = {s: [assemble(p, hex_px=128, pad=4) for p in dirs[s]] for s in dirs}
    small = {s: [assemble(p, hex_px=64, pad=2) for p in dirs[s]] for s in dirs}
    col_w = {s: max(i.width for i in big[s]) + PAD for s in dirs}
    row_h = max(i.height for v in big.values() for i in v) + PAD
    srow_h = max(i.height for v in small.values() for i in v) + PAD
    w = 60 + sum(col_w.values()) + PAD
    h = 44 + rows * row_h + 20 + rows * srow_h
    im = on_bg((w, h))
    d = ImageDraw.Draw(im)
    d.text((PAD, 4), "base stages 1-4 (footprint grows (0,0) +(1,0) +(0,1) +(1,1)); 128 px, 64 px below", font=F, fill=FG)
    x = 60
    for s in dirs:
        d.text((x, 24), f"stage {s}", font=F, fill=FG)
        for r, (b, sm) in enumerate(zip(big[s], small[s])):
            im.paste(b, (x, 44 + r * row_h))
            im.paste(sm, (x, 44 + rows * row_h + 20 + r * srow_h))
            d.text((x + 2, 44 + r * row_h + 2), dirs[s][r].name.split("_")[1], font=FS, fill=FG)
        x += col_w[s]
    for r in range(rows):
        d.text((PAD, 44 + r * row_h + 40), f"v{r}", font=F, fill=FG)
    d.text((PAD, 44 + rows * row_h + 2), "64 px:", font=FS, fill=FG)
    return im


def base_measures() -> dict[str, dict]:
    """Occupied share over each stage's whole footprint (same matte as single hexes)."""
    hexm = tile_distance() >= 1.0
    out = {}
    for s, dirs in stage_dirs().items():
        for p in dirs:
            pos = json.loads((p / "positions.json").read_text())
            src = REPO / pos["source"]
            occ, n = 0.0, 0
            for (q, r) in pos["positions"].values():
                if src.suffix == ".png":  # a single-hex level tile reused as a stage-1 candidate
                    work = src.parents[1]
                    side = json.loads(next(work.glob("*.sheet.json")).read_text())
                    c = next(c for c in side["cells"] if c["output"] == src.name)
                    base_p, cut_p = REPO / c["base"], work / "cut" / c["output"]
                else:
                    work = src.parents[1]
                    cluster = src.name
                    base_p = work / "bases" / cluster / f"q{q}_r{r}.png"
                    cut_p = work / "cut" / cluster / f"q{q}_r{r}.png"
                base = np.asarray(Image.open(base_p).convert("RGB"), dtype=float)
                cut = np.asarray(Image.open(cut_p).convert("RGB"), dtype=float)
                matte = ground_restore(cut, base, PinConfig.from_json({"restore": True}))[1]
                occ += float((matte[hexm] > 0.5).sum())
                n += int(hexm.sum())
            out[p.name] = {"stage": s, "hexes": len(pos["positions"]), "occupied": round(occ / n, 3)}
    return out


def single_measures() -> dict[str, list[dict]]:
    out = {}
    for kind in KINDS:
        picks = json.loads((CAND / kind / "picks.json").read_text())["picks"]
        out[kind] = [
            {k: p[k] for k in ("file", "level", "occupied", "built", "L", "gate_passed", "failed_checks")}
            for p in picks
        ]
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        kind_sheet(kind).convert("RGB").save(OUT / f"{kind}.png")
    all_kinds_64().convert("RGB").save(OUT / "all-kinds-64.png")
    emblems_vs_l2().convert("RGB").save(OUT / "emblems-vs-l2.png")
    base_stages().convert("RGB").save(OUT / "base-stages.png")
    (OUT / "base-stages.json").write_text(json.dumps(base_measures(), indent=1) + "\n")
    (OUT / "measures.json").write_text(json.dumps(single_measures(), indent=1) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
