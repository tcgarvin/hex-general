"""Build the sites2 review images (round 2 beside round 1).

Writes into art/codex/terrain/review/sites2/:
  <kind>.png              columns = level; first row = round 1 (v0), then round-2
                          variants at 128 px; 64 px strips (r1 then r2) beneath;
                          the kind's emblem beside its round-2 L2 tile
  all-kinds-64.png        town reference row + every kind at every level + base stages, 64 px
  all-kinds-64-gray.png   the same in greyscale (value-only legibility)
  confusion-64.png        the round-1 confusable pairs, r1 vs r2, colour and grey, 64 px
  base-stages.png         base stages 1-4 (round 2), 128 px with 64 px strip; round-1 row for reference
  measures.json           per tile metrics (sites2_measure.tile_metrics) for r1, r2 and the town;
                          base perimeter inset profiles for r1 and r2 stages

usage: python3 art/codex/terrain/sites2_review.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from paintin_cluster import assemble  # noqa: E402
from sites2_measure import perimeter_inset, tile_metrics  # noqa: E402

R1 = HERE / "candidates" / "sites1"
R2 = HERE / "candidates" / "sites2"
OUT = HERE / "review" / "sites2"
EMBLEMS = REPO / "frontend" / "src" / "world" / "sites"
TOWN = REPO / "frontend" / "src" / "board" / "tiles" / "town"
CITY = REPO / "frontend" / "src" / "board" / "tiles" / "city"
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


def by_level(root: Path, kind: str) -> dict[int, list[Path]]:
    return {lv: sorted((root / kind).glob(f"l{lv}_*.png")) for lv in (1, 2, 3)}


def stage_dirs(root: Path) -> dict[int, list[Path]]:
    return {s: sorted((root / "base").glob(f"stage{s}_*")) for s in (1, 2, 3, 4)}


def canvas(w: int, h: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    im = Image.new("RGBA", (w, h), BG + (255,))
    return im, ImageDraw.Draw(im)


def kind_sheet(kind: str) -> Image.Image:
    r1, r2 = by_level(R1, kind), by_level(R2, kind)
    rows = [("r1", {lv: r1[lv][:1] for lv in r1})] + [
        (f"r2 v{i}", {lv: r2[lv][i : i + 1] for lv in r2}) for i in range(max(len(v) for v in r2.values()))
    ]
    col_w = 128 + PAD
    lab = 64
    ex = lab + 3 * col_w + PAD
    w = ex + 2 * (128 + PAD) + PAD
    strip_h = 2 * (64 + 18)
    h = 42 + len(rows) * (128 + PAD) + strip_h + PAD
    im, d = canvas(w, h)
    d.text((PAD, 4), f"{kind}: round 1 (top row) vs round 2; columns = level", font=F, fill=FG)
    for c, lv in enumerate((1, 2, 3)):
        d.text((lab + c * col_w + 40, 24), f"level {lv}", font=F, fill=FG)
    for r, (name, cells) in enumerate(rows):
        y = 42 + r * (128 + PAD)
        d.text((PAD, y + 56), name, font=FS, fill=FG)
        for c, lv in enumerate((1, 2, 3)):
            for p in cells[lv]:
                im.alpha_composite(tile(p, 128), (lab + c * col_w, y))
    d.text((ex, 24), "emblem | r2 L2", font=F, fill=FG)
    im.alpha_composite(tile(EMBLEMS / f"{kind}.png", 128), (ex, 42 + 128 + PAD))
    im.alpha_composite(tile(r2[2][0], 128), (ex + 128 + PAD, 42 + 128 + PAD))
    im.alpha_composite(tile(EMBLEMS / f"{kind}.png", 64), (ex, 42 + 2 * (128 + PAD)))
    im.alpha_composite(tile(r2[2][0], 64), (ex + 64 + 4, 42 + 2 * (128 + PAD)))
    y = 42 + len(rows) * (128 + PAD)
    for name, src in (("r1 64 px", r1), ("r2 64 px", r2)):
        d.text((PAD, y + 24), name, font=FS, fill=FG)
        x = lab
        for lv in (1, 2, 3):
            for p in src[lv]:
                im.alpha_composite(tile(p, 64), (x, y))
                x += 66
            x += 12
        y += 64 + 18
    return im


def all_kinds_64(root: Path) -> Image.Image:
    lab, cell = 96, 64 + 14
    stages = [assemble(stage_dirs(root)[s][0], hex_px=64, pad=0) for s in (1, 2, 3, 4)]
    rows: list[tuple[str, list[Path]]] = [("town / city\n(shipped)", [TOWN / "00.png", TOWN / "02.png", CITY / "00.png"])]
    rows += [(k, [by_level(root, k)[lv][0] for lv in (1, 2, 3)]) for k in KINDS]
    w = max(lab + 3 * cell, lab + sum(s.width + 14 for s in stages)) + PAD
    h = 38 + len(rows) * (64 + 10) + max(s.height for s in stages) + 24
    im, d = canvas(w, h)
    d.text((PAD, 4), f"all kinds at 64 px ({root.name}, first variant)", font=F, fill=FG)
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


def confusion_64() -> Image.Image:
    def pick(root: Path, kind: str, lv: int) -> Path:
        return by_level(root, kind)[lv][0]

    def base_hex(root: Path, stage: int) -> Path:
        return stage_dirs(root)[stage][0] / "h0.png"

    pairs = [
        ("oil L1 | steel L1", lambda r: [pick(r, "oil_field", 1), pick(r, "steel_mill", 1)]),
        ("farm L1, L2 | town", lambda r: [pick(r, "farm", 1), pick(r, "farm", 2), TOWN / "00.png"]),
        ("factory L2, L3 | base S2, S3 hex", lambda r: [pick(r, "factory", 2), pick(r, "factory", 3), base_hex(r, 2), base_hex(r, 3)]),
        ("oil L3 | steel L2", lambda r: [pick(r, "oil_field", 3), pick(r, "steel_mill", 2)]),
        ("steel L3 | city", lambda r: [pick(r, "steel_mill", 3), CITY / "00.png"]),
    ]
    cols = [("r1 colour", R1, False), ("r2 colour", R2, False), ("r1 grey", R1, True), ("r2 grey", R2, True)]
    blk = 4 * 66 + 16
    w = 190 + len(cols) * blk
    h = 40 + len(pairs) * (64 + 12)
    im, d = canvas(w, h)
    d.text((PAD, 4), "round-1 confusable pairs at 64 px: round 1 vs round 2, colour and greyscale", font=F, fill=FG)
    for c, (name, _, _) in enumerate(cols):
        d.text((190 + c * blk, 22), name, font=FS, fill=FG)
    for r, (name, fn) in enumerate(pairs):
        y = 40 + r * (64 + 12)
        d.text((PAD, y + 24), name, font=FS, fill=FG)
        for c, (_, root, grey) in enumerate(cols):
            for i, p in enumerate(fn(root)):
                t = tile(p, 64)
                if grey:
                    g = ImageOps.grayscale(t.convert("RGB")).convert("RGBA")
                    g.putalpha(t.getchannel("A"))
                    t = g
                im.alpha_composite(t, (190 + c * blk + i * 66, y))
    return im


def base_stages() -> Image.Image:
    rows = [("r1 a", R1, 0)] + [(f"r2 {v}", R2, i) for i, v in enumerate("ab")]
    big = {(n, s): assemble(stage_dirs(root)[s][i], hex_px=128, pad=4) for n, root, i in rows for s in (1, 2, 3, 4)}
    small = {(n, s): assemble(stage_dirs(root)[s][i], hex_px=64, pad=2) for n, root, i in rows for s in (1, 2, 3, 4)}
    col_w = {s: max(big[(n, s)].width for n, _, _ in rows) + PAD for s in (1, 2, 3, 4)}
    row_h = max(i.height for i in big.values()) + PAD
    srow_h = max(i.height for i in small.values()) + PAD
    w = 60 + sum(col_w.values()) + PAD
    h = 44 + len(rows) * row_h + 20 + len(rows) * srow_h
    im, d = canvas(w, h)
    d.text((PAD, 4), "base stages 1-4: footprint (0,0) +(1,0) +(0,1) +(1,1); 128 px, 64 px below; top row = round 1", font=F, fill=FG)
    x = 60
    for s in (1, 2, 3, 4):
        d.text((x, 24), f"stage {s}", font=F, fill=FG)
        for r, (n, _, _) in enumerate(rows):
            im.paste(big[(n, s)], (x, 44 + r * row_h))
            im.paste(small[(n, s)], (x, 44 + len(rows) * row_h + 20 + r * srow_h))
        x += col_w[s]
    for r, (n, _, _) in enumerate(rows):
        d.text((PAD, 44 + r * row_h + 40), n, font=F, fill=FG)
        d.text((PAD, 44 + len(rows) * row_h + 20 + r * srow_h + 20), n, font=FS, fill=FG)
    return im


def measures() -> dict:
    out: dict = {"town": {p.name: tile_metrics(p) for p in sorted(TOWN.glob("*.png"))}}
    for tag, root in (("sites1", R1), ("sites2", R2)):
        out[tag] = {
            kind: {p.name: tile_metrics(p) for lv in (1, 2, 3) for p in by_level(root, kind)[lv]} for kind in KINDS
        }
        out[tag]["base_perimeter"] = {
            p.name: perimeter_inset(p) for s in (2, 3, 4) for p in stage_dirs(root)[s]
        }
        out[tag]["base_stage1"] = {p.name: tile_metrics(p / "h0.png") for p in stage_dirs(root)[1]}
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        kind_sheet(kind).convert("RGB").save(OUT / f"{kind}.png")
    ak = all_kinds_64(R2).convert("RGB")
    ak.save(OUT / "all-kinds-64.png")
    ImageOps.grayscale(ak).save(OUT / "all-kinds-64-gray.png")
    confusion_64().convert("RGB").save(OUT / "confusion-64.png")
    base_stages().convert("RGB").save(OUT / "base-stages.png")
    (OUT / "measures.json").write_text(json.dumps(measures(), indent=1) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
