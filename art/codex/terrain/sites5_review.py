"""Review renders for sites round 5 (factory ladder, oil L3, one-hex base).

Reads `candidates/sites-r5/candidates.json` (targets + labelled candidates) and
writes, per target, into `--out-dir`:

  <target>-3x.png     the shipped tiles of that level (plus the neighbouring
                      level for the ladder) and every KEPT candidate, 3x, labelled
  <target>-board.png  board-scale crops of `scripts/board-mock-maps/sites5-ladder.txt`
                      (64 px per hex): the shipped set first, then one crop per
                      kept candidate with that candidate as the level's only
                      variant (a temporary sites dir under `--scratch`, built
                      from symlinks into the ratified set; the ratified dir is
                      never written)
  before-board-64.png the whole ladder board with the shipped set
  picks-board-64.png  the same board with every target's first-pass pick swapped in

usage: python3 art/codex/terrain/sites5_review.py --scratch <dir> --out-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RATIFIED = HERE / "candidates" / "sites-ratified"
ROUND = HERE / "candidates" / "sites-r5"
MAP = REPO / "scripts" / "board-mock-maps" / "sites5-ladder.txt"
JOINS = REPO / "frontend" / "src" / "board" / "joins"
BOARD_MOCK = REPO / "scripts" / "board-mock.py"

SCALE = 3
TILE = 128 * SCALE
GAP = 12
LABEL_H = 26
BG = (40, 40, 48)
CHECKER = ((80, 80, 90), (110, 110, 120))
INK = (255, 236, 160)
DIM = (190, 190, 200)


def checker(w: int, h: int, cell: int = 12) -> Image.Image:
    im = Image.new("RGB", (w, h), CHECKER[0])
    d = ImageDraw.Draw(im)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if (x // cell + y // cell) % 2:
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=CHECKER[1])
    return im


def tile_card(path: Path, label: str, scale: int = SCALE) -> Image.Image:
    """One tile at `scale`x on a checker, with a label strip above it."""
    size = 128 * scale
    t = Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    card = Image.new("RGB", (size, size + LABEL_H), BG)
    bg = checker(size, size)
    bg.paste(t, (0, 0), t)
    card.paste(bg, (0, LABEL_H))
    ImageDraw.Draw(card).text((4, 6), label, fill=INK)
    return card


def grid(rows: list[tuple[str, list[Image.Image]]], cols: int) -> Image.Image:
    """Titled rows of equal-size cards, wrapped at `cols` per line."""
    cw, ch = rows[0][1][0].size
    lines: list[tuple[str, list[Image.Image]]] = []
    for title, cards in rows:
        for i in range(0, len(cards), cols):
            lines.append((title if i == 0 else "", cards[i : i + cols]))
    w = GAP + cols * (cw + GAP)
    h = GAP + sum(LABEL_H + ch + GAP for _ in lines)
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    y = GAP
    for title, cards in lines:
        d.text((GAP, y + 6), title, fill=DIM)
        y += LABEL_H
        for i, c in enumerate(cards):
            im.paste(c, (GAP + i * (cw + GAP), y))
        y += ch + GAP
    return im


def stack(items: list[tuple[str, Image.Image]], cols: int) -> Image.Image:
    """Labelled images in a grid (all the same size)."""
    cw, ch = items[0][1].size
    rows = (len(items) + cols - 1) // cols
    im = Image.new("RGB", (GAP + cols * (cw + GAP), GAP + rows * (LABEL_H + ch + GAP)), BG)
    d = ImageDraw.Draw(im)
    for i, (label, img) in enumerate(items):
        r, c = divmod(i, cols)
        x = GAP + c * (cw + GAP)
        y = GAP + r * (LABEL_H + ch + GAP)
        d.text((x, y + 6), label, fill=INK)
        im.paste(img, (x, y + LABEL_H))
    return im


def link_tree(dst: Path) -> None:
    """dst = the ratified sites tree as symlinks (files only), so it can be edited."""
    if dst.exists():
        shutil.rmtree(dst)
    for src in RATIFIED.rglob("*"):
        if src.is_file() and src.suffix in (".png", ".json"):
            out = dst / src.relative_to(RATIFIED)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.symlink_to(src)


def swap_in(sites: Path, kind: str, level: int, tile: Path) -> None:
    """Make `tile` the only variant of (kind, level) in the temp sites dir."""
    if kind == "base":
        for d in (sites / "base").glob(f"stage{level}_*"):
            shutil.rmtree(d)
        d = sites / "base" / f"stage{level}_r5"
        d.mkdir(parents=True)
        (d / "h0.png").symlink_to(tile.resolve())
        (d / "positions.json").write_text(json.dumps({"positions": {"h0": [0, 0]}}))
        return
    for p in (sites / kind).glob(f"l{level}_*.png"):
        p.unlink()
    (sites / kind / f"l{level}_00.png").symlink_to(tile.resolve())


def board(sites: Path, crop: str, out: Path) -> Image.Image:
    cmd = [
        sys.executable,
        str(BOARD_MOCK),
        "--map",
        str(MAP),
        "--sites",
        str(sites),
        "--joins",
        str(JOINS),
        "--hex-px",
        "64",
        "--out",
        str(out),
    ]
    if crop:
        cmd += ["--crop", crop]
    subprocess.run(cmd, check=True, capture_output=True)
    return Image.open(out).convert("RGB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    spec = json.loads((ROUND / "candidates.json").read_text())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    # before: the shipped set on the ladder board
    shipped_sites = args.scratch / "sites-shipped"
    link_tree(shipped_sites)
    full = board(shipped_sites, "", args.scratch / "before-board.png")
    full.save(args.out_dir / "before-board-64.png")

    # after: every target's first-pass pick swapped in at once
    picked = args.scratch / "sites-picks"
    link_tree(picked)
    by_label = {c["label"]: c for c in spec["candidates"] if c["kept"]}
    for t in spec["targets"].values():
        swap_in(picked, t["kind"], t["level"], ROUND / by_label[t["first_pass_pick"]]["tile"])
    board(picked, "", args.scratch / "picks-board.png").save(args.out_dir / "picks-board-64.png")

    for name, t in spec["targets"].items():
        kind, level = t["kind"], t["level"]
        shipped = [RATIFIED / p for p in t["shipped"]]
        context = [RATIFIED / p for p in t.get("context", [])]
        cands = [c for c in spec["candidates"] if c["target"] == name and c["kept"]]
        rows = [
            (
                f"shipped {kind} level {level}",
                [tile_card(p, f"shipped {p.parent.name}/{p.name}") for p in shipped],
            )
        ]
        if context:
            rows.append(
                (
                    t.get("context_title", "for comparison"),
                    [tile_card(p, f"shipped {p.parent.name}/{p.name}") for p in context],
                )
            )
        if cands:
            rows.append(
                (
                    "round 5 candidates",
                    [tile_card(ROUND / c["tile"], c["label"]) for c in cands],
                )
            )
        grid(rows, 4).save(args.out_dir / f"{name}-3x.png")

        crops = [("shipped (variant by coordinate hash)", board(shipped_sites, t["crop"], args.scratch / f"{name}-shipped.png"))]
        for c in cands:
            sites = args.scratch / f"sites-{c['label']}"
            link_tree(sites)
            swap_in(sites, kind, level, ROUND / c["tile"])
            crops.append((c["label"], board(sites, t["crop"], args.scratch / f"{name}-{c['label']}.png")))
        stack(crops, t.get("board_cols", 2)).save(args.out_dir / f"{name}-board.png")
        print(f"{name}: {len(cands)} kept candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
