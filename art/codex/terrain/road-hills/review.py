#!/usr/bin/env python3
"""Review images for the roads-over-hills candidates.

1. `review/hills-roads/shapes.png`: every tile at 128 px and 64 px next to the
   bare hills tile it was painted on (same two scales).
2. `review/hills-roads/board-{128,64}px.png`: `scripts/board-mock-maps/hills-roads.txt`
   rendered from a temporary merged tiles dir (candidates/review1 + these roads),
   no grid, no vector roads.
3. `review/hills-roads/web/`: web-size copies (JPEG q90 when a PNG is over 1.2 MB).

    python3 art/codex/terrain/road-hills/review.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
TERRAIN = HERE.parent
REPO = TERRAIN.parents[2]
OUT = TERRAIN / "review" / "hills-roads"
TILES = TERRAIN / "candidates" / "hills-roads" / "road"
HILLS = TERRAIN / "candidates" / "review1" / "hills"
BG = (30, 30, 34)
WEB_MAX = 1_200_000


def on_bg(path: Path, px: int) -> Image.Image:
    im = Image.open(path).convert("RGBA").resize((px, px), Image.Resampling.LANCZOS)
    bg = Image.new("RGBA", im.size, BG + (255,))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def shapes_montage() -> Path:
    prov = json.loads((TILES / "provenance.json").read_text())
    names = sorted(prov)
    font = ImageFont.load_default(size=13)
    pad, lab, cols = 8, 18, 2
    cell_w = 2 * (128 + pad) + 2 * (64 + pad) + 24
    cell_h = 128 + lab + pad
    rows = (len(names) + cols - 1) // cols
    out = Image.new("RGB", (cols * cell_w + pad, 30 + rows * cell_h), BG)
    d = ImageDraw.Draw(out)
    d.text((pad, 6), "roads over hills: tile | bare hills base, at 128 px then 64 px",
           fill=(240, 240, 240), font=ImageFont.load_default(size=15))
    for i, name in enumerate(names):
        r, c = divmod(i, cols)
        x, y = pad + c * cell_w, 30 + r * cell_h
        base = HILLS / Path(prov[name]["hills_base"]).name.replace("h", "", 1)
        d.text((x, y), f"{Path(name).stem}  (on hills/{base.name})", fill=(230, 220, 150), font=font)
        for px in (128, 64):
            for p in (TILES / name, base):
                out.paste(on_bg(p, px), (x, y + lab))
                x += px + pad
            x += 12
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "shapes.png"
    out.save(path)
    return path


def board() -> list[Path]:
    paths = []
    with tempfile.TemporaryDirectory(prefix="hills-roads-") as tmp:
        merged = Path(tmp) / "tiles"
        shutil.copytree(TERRAIN / "candidates" / "review1", merged,
                        ignore=shutil.ignore_patterns("*.json"))
        for p in TILES.glob("hills_*.png"):
            shutil.copyfile(p, merged / "road" / p.name)
        for px in (128, 64):
            path = OUT / f"board-{px}px.png"
            subprocess.run(
                [sys.executable, str(REPO / "scripts" / "board-mock.py"), "--tiles", str(merged),
                 "--map", str(REPO / "scripts" / "board-mock-maps" / "hills-roads.txt"),
                 "--out", str(path), "--hex-px", str(px), "--no-grid", "--no-vector-roads"],
                check=True,
            )
            paths.append(path)
    return paths


def web(paths: list[Path]) -> None:
    (OUT / "web").mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.stat().st_size > WEB_MAX:
            Image.open(p).convert("RGB").save(OUT / "web" / f"{p.stem}.jpg", quality=90)
        else:
            shutil.copyfile(p, OUT / "web" / p.name)


def main() -> None:
    paths = [shapes_montage(), *board()]
    web(paths)
    for p in paths:
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
