"""Build the sites3 review set (the best-of shown to the user).

Writes into art/codex/terrain/review/sites3/:
  <kind>.png                   final picks: columns = level, rows = variants, 128 px, 64 px strip
  all-kinds-64.png / -gray     shipped town + city row, every kind at every level, base stages
  base-stages.png              final base stages 1-4, both variants, 128 px with 64 px strip
  base-perimeter-options.png   stages 3 and 4 in three perimeter treatments (rounds 1, 2, 3)
  levels-material.png          per kind L1, L2, L3 at 256 px
  measures.json                tile metrics (sites2_measure) + base perimeter inset profiles

usage: python3 art/codex/terrain/sites3_review.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sites2_measure import perimeter_inset, tile_metrics  # noqa: E402
from sites2_review import (  # noqa: E402
    CITY,
    FG,
    FS,
    F,
    KINDS,
    PAD,
    TOWN,
    assemble,
    by_level,
    canvas,
    stage_dirs,
    tile,
)

R1 = HERE / "candidates" / "sites1"
R2 = HERE / "candidates" / "sites2"
R3 = HERE / "candidates" / "sites3"
OUT = HERE / "review" / "sites3"


def kind_sheet(kind: str) -> Image.Image:
    levels = by_level(R3, kind)
    rows = max(len(v) for v in levels.values())
    lab, col_w = 50, 128 + PAD
    n = sum(len(v) for v in levels.values())
    w = max(lab + 3 * col_w, lab + n * 66 + 2 * 12) + PAD
    h = 42 + rows * (128 + PAD) + 64 + 30
    im, d = canvas(w, h)
    d.text((PAD, 4), f"{kind}: final picks; columns = level, rows = variants", font=F, fill=FG)
    for c, lv in enumerate((1, 2, 3)):
        d.text((lab + c * col_w + 40, 24), f"level {lv}", font=F, fill=FG)
        for r, p in enumerate(levels[lv]):
            im.alpha_composite(tile(p, 128), (lab + c * col_w, 42 + r * (128 + PAD)))
    for r in range(rows):
        d.text((PAD, 42 + r * (128 + PAD) + 56), f"v{r}", font=FS, fill=FG)
    y = 42 + rows * (128 + PAD) + 6
    d.text((PAD, y + 24), "64 px", font=FS, fill=FG)
    x = lab
    for lv in (1, 2, 3):
        for p in levels[lv]:
            im.alpha_composite(tile(p, 64), (x, y))
            x += 66
        x += 12
    return im


def all_kinds_64() -> Image.Image:
    lab, cell = 96, 64 + 14
    stages = [assemble(stage_dirs(R3)[s][0], hex_px=64, pad=0) for s in (1, 2, 3, 4)]
    rows: list[tuple[str, list[Path]]] = [
        ("town / city\n(shipped)", [TOWN / "00.png", TOWN / "02.png", CITY / "00.png", CITY / "02.png"])
    ]
    rows += [(k, [by_level(R3, k)[lv][0] for lv in (1, 2, 3)]) for k in KINDS]
    w = max(lab + 4 * cell, lab + sum(s.width + 14 for s in stages)) + PAD
    h = 38 + len(rows) * (64 + 10) + max(s.height for s in stages) + 24
    im, d = canvas(w, h)
    d.text((PAD, 4), "all kinds at 64 px (final set, first variant)", font=F, fill=FG)
    for c, lv in enumerate((1, 2, 3)):
        d.text((lab + c * cell + 20, 22), f"L{lv}", font=FS, fill=FG)
    for r, (name, paths) in enumerate(rows):
        y = 38 + r * (64 + 10)
        d.text((PAD, y + 20), name, font=FS, fill=FG)
        for c, p in enumerate(paths):
            im.alpha_composite(tile(p, 64), (lab + c * cell, y))
    y = 38 + len(rows) * (64 + 10)
    d.text((PAD, y + 20), "base\nstages 1-4", font=FS, fill=FG)
    x = lab
    for s, st in enumerate(stages, start=1):
        d.text((x, y), f"S{s}", font=FS, fill=FG)
        im.paste(st, (x, y + 14))
        x += st.width + 14
    return im


def base_stages() -> Image.Image:
    dirs = stage_dirs(R3)
    nrow = max(len(v) for v in dirs.values())
    big = {(s, i): assemble(p, hex_px=128, pad=4) for s in dirs for i, p in enumerate(dirs[s])}
    small = {(s, i): assemble(p, hex_px=64, pad=2) for s in dirs for i, p in enumerate(dirs[s])}
    col_w = {s: max(big[(s, i)].width for i in range(len(dirs[s]))) + PAD for s in dirs}
    row_h = max(i.height for i in big.values()) + PAD
    srow_h = max(i.height for i in small.values()) + PAD
    w = 50 + sum(col_w.values()) + PAD
    h = 44 + nrow * row_h + 20 + nrow * srow_h
    im, d = canvas(w, h)
    d.text((PAD, 4), "base stages 1-4 (final): footprint (0,0) +(1,0) +(0,1) +(1,1); 128 px, 64 px below", font=F, fill=FG)
    x = 50
    for s in dirs:
        d.text((x, 24), f"stage {s}", font=F, fill=FG)
        for i in range(len(dirs[s])):
            im.paste(big[(s, i)], (x, 44 + i * row_h))
            im.paste(small[(s, i)], (x, 44 + nrow * row_h + 20 + i * srow_h))
        x += col_w[s]
    for i in range(nrow):
        d.text((PAD, 44 + i * row_h + 40), "ab"[i], font=F, fill=FG)
        d.text((PAD, 44 + nrow * row_h + 20 + i * srow_h + 20), "ab"[i], font=FS, fill=FG)
    return im


def perimeter_options() -> Image.Image:
    rows = [
        ("A - outline bank (round 1)", R1),
        ("B - straight fence (round 2)", R2),
        ("C - organic bank (round 3)", R3),
    ]
    imgs = [[assemble(stage_dirs(root)[s][0], hex_px=128, pad=4) for s in (3, 4)] for _, root in rows]
    w = max(a.width + b.width for a, b in imgs) + 3 * PAD
    row_h = max(max(a.height, b.height) for a, b in imgs) + 28
    im, d = canvas(w, 30 + len(rows) * row_h)
    d.text((PAD, 4), "base perimeter treatments: stage 3 (left) and stage 4 (right), 128 px", font=F, fill=FG)
    for r, ((name, _), (a, b)) in enumerate(zip(rows, imgs)):
        y = 30 + r * row_h
        d.text((PAD, y), name, font=F, fill=FG)
        im.paste(a, (PAD, y + 20))
        im.paste(b, (2 * PAD + a.width, y + 20))
    return im


def levels_material() -> Image.Image:
    lab, px = 110, 256
    w = lab + 3 * (px + PAD) + PAD
    im, d = canvas(w, 30 + len(KINDS) * (px + PAD))
    d.text((PAD, 4), "material by level at 256 px: L1 makeshift -> L2 brick -> L3 works", font=F, fill=FG)
    for r, kind in enumerate(KINDS):
        y = 30 + r * (px + PAD)
        d.text((PAD, y + px // 2), kind, font=F, fill=FG)
        for c, lv in enumerate((1, 2, 3)):
            t = Image.open(by_level(R3, kind)[lv][0]).convert("RGBA").resize((px, px), Image.Resampling.LANCZOS)
            im.alpha_composite(t, (lab + c * (px + PAD), y))
            d.text((lab + c * (px + PAD) + 4, y + 4), f"L{lv}", font=F, fill=FG)
    return im


def measures() -> dict:
    out: dict = {"town": {p.name: tile_metrics(p) for p in sorted(TOWN.glob("*.png"))}}
    out["sites3"] = {
        kind: {p.name: tile_metrics(p) for lv in (1, 2, 3) for p in by_level(R3, kind)[lv]} for kind in KINDS
    }
    out["sites3"]["base_perimeter"] = {p.name: perimeter_inset(p) for s in (2, 3, 4) for p in stage_dirs(R3)[s]}
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        kind_sheet(kind).convert("RGB").save(OUT / f"{kind}.png")
    ak = all_kinds_64().convert("RGB")
    ak.save(OUT / "all-kinds-64.png")
    ImageOps.grayscale(ak).save(OUT / "all-kinds-64-gray.png")
    base_stages().convert("RGB").save(OUT / "base-stages.png")
    perimeter_options().convert("RGB").save(OUT / "base-perimeter-options.png")
    levels_material().convert("RGB").save(OUT / "levels-material.png")
    (OUT / "measures.json").write_text(json.dumps(measures(), indent=1) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
