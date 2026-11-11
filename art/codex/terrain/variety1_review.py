#!/usr/bin/env python3
"""Review renders and page for variety round 1 (plains features, homesteads,
roadside buildings; candidates, unratified).

Reads `candidates/variety-r1/candidates.json` and the kept tiles, and writes into
`--out-dir`:

  board-<win>-<density>.png   the two real map windows (`scripts/board-mock-maps/
                              variety-{open,town}.txt`, exported with
                              `scripts/road-study.py export`) at 64 px per hex:
                              shipped tiles ("before") and variety low/medium/high
  zoom-town-{before,medium}.png  a crop of the town window at 128 px per hex
  units-{64,128}.png          the medium town render with unit sprites
                              (`frontend/src/board/units/*_{base,mask}.png`, drawn
                              like `WorldRenderer.placeUnitSprite`: 1.5 x HEX_SIZE
                              wide, centred, mask tinted by owner colour) on
                              decorated hexes, composited here (board-mock draws no
                              units)
  sheet-*.png                 3x candidate sheets with labels and gate results, each
                              next to the shipped tiles it varies; mirrored road
                              shapes also shown as drawn mirrored
  patches-64.png              every kept nature/home tile at 64 px inside a ring of
                              shipped plains
  dropped-2x.png              gate-passing cells not kept and gate failures, 2x

then bundles them with `scripts/review-page.py` into `--page` and inserts the notes.

usage: python3 art/codex/terrain/variety1_review.py --out-dir <dir> --page <html>
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROUND = HERE / "candidates" / "variety-r1"
KEPT = ROUND / "kept"
SHIPPED = REPO / "frontend" / "src" / "board" / "tiles"
UNITS = REPO / "frontend" / "src" / "board" / "units"
JOINS = REPO / "frontend" / "src" / "board" / "joins"
SITES = HERE / "candidates" / "sites-ratified"
MAPS = REPO / "scripts" / "board-mock-maps"
WINDOWS = ("open", "town")
DENSITIES = ("low", "medium", "high")
ZOOM_CROP = (5, 1, 15, 9)  # town window cols/rows around the town junction
MIRRORED_SHAPES = ("straight_d", "turn_n_se", "turn_s_nw")
OWNER_COLOURS = ((0xB8, 0x47, 0x3A), (0x3E, 0x6B, 0xA8))

SCALE = 3
GAP = 12
LABEL_H = 22
GATE_H = 18
BG = (40, 40, 48)
INK = (255, 236, 160)
DIM = (190, 190, 200)
BAD = (255, 140, 120)


def board_mock():
    spec = importlib.util.spec_from_file_location(
        "board_mock", REPO / "scripts" / "board-mock.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["board_mock"] = module
    spec.loader.exec_module(module)
    return module


BM = board_mock()


# ----------------------------------------------------------------- boards ----


def render(window: str, density: str | None, hex_px: int, crop=None):
    game_map = BM.load_map(MAPS / f"variety-{window}.txt")
    library = BM.TileLibrary(BM.TileSet.discover(SHIPPED, "tiles"), None)
    image, report = BM.render(
        game_map,
        library,
        hex_px,
        False,
        crop,
        joins=BM.JoinSet.load(JOINS),
        draw_vector_roads=False,
        sites=BM.SiteSet.discover(SITES),
        variety=None if density is None else BM.VarietySet.discover(KEPT),
        variety_density=density or "medium",
    )
    return game_map, image.convert("RGB"), report


def screen_centres(
    game_map, hex_px: int, crop
) -> dict[tuple[int, int], tuple[float, float]]:
    """Hex centre -> image px, as `BM.render` places them."""
    c0, r0, c1, r1 = crop if crop else (0, 0, game_map.width - 1, game_map.height - 1)
    shown = [h for h in game_map.hexes if c0 <= h.col <= c1 and r0 <= h.row <= r1]
    centres = {(h.q, h.r): BM.axial_to_pixel(h.q, h.r, BM.HEX_SIZE) for h in shown}
    min_x = min(x for x, _ in centres.values()) - BM.HEX_SIZE
    min_y = min(y for _, y in centres.values()) - BM.HEX_SIZE
    zoom = hex_px / (2 * BM.HEX_SIZE)
    return {
        k: ((x - min_x) * zoom, (y - min_y) * zoom) for k, (x, y) in centres.items()
    }


def unit_sprite(
    kind: str, colour: tuple[int, int, int], width: int, mirror: bool
) -> Image.Image:
    base = Image.open(UNITS / f"{kind}_base.png").convert("RGBA")
    mask = np.asarray(
        Image.open(UNITS / f"{kind}_mask.png").convert("RGBA"), dtype=np.float64
    )
    mask[..., :3] *= np.array(colour) / 255.0
    tinted = Image.fromarray(np.clip(np.round(mask), 0, 255).astype(np.uint8), "RGBA")
    out = Image.alpha_composite(base, tinted).resize((width, width), Image.LANCZOS)
    return ImageOps.mirror(out) if mirror else out


def with_units(window: str, hex_px: int, crop) -> Image.Image:
    """The medium render with units on decorated hexes (and one plain hex)."""
    game_map, image, report = render(window, "medium", hex_px, crop)
    centres = screen_centres(game_map, hex_px, crop)
    decorated = sorted(
        ((h.q, h.r), p.kind) for h, p in report.variety if (h.q, h.r) in centres
    )
    order = []  # up to three homes, two roadside, three nature
    for kind, n in (("home", 3), ("road", 2), ("nature", 3)):
        of_kind = [d for d in decorated if d[1] == kind]
        step = max(1, len(of_kind) // n)  # spread over the window
        order += of_kind[::step][:n]
    kinds = ("infantry", "armor", "artillery")
    width = round(1.5 * BM.HEX_SIZE * hex_px / (2 * BM.HEX_SIZE))
    out = image.convert("RGBA")
    for i, (at, _) in enumerate(order):
        x, y = centres[at]
        sprite = unit_sprite(
            kinds[i % 3], OWNER_COLOURS[i % 2], width, mirror=bool(i % 2)
        )
        out.alpha_composite(sprite, (round(x - width / 2), round(y - width / 2)))
    return out.convert("RGB")


# ----------------------------------------------------------------- sheets ----


def card(
    path: Path,
    label: str,
    gate: str,
    ok: bool,
    mirror: bool = False,
    scale: int = SCALE,
) -> Image.Image:
    tile = Image.open(path).convert("RGBA")
    if mirror:
        tile = ImageOps.mirror(tile)
    size = 128 * scale
    tile = tile.resize((size, size), Image.LANCZOS)
    im = Image.new("RGB", (size, LABEL_H + size + GATE_H), BG)
    im.paste(tile, (0, LABEL_H), tile)
    d = ImageDraw.Draw(im)
    d.text((4, 5), label, fill=INK)
    d.text((4, LABEL_H + size + 3), gate, fill=DIM if ok else BAD)
    return im


def grid(cards: list[list[Image.Image]], title: str) -> Image.Image:
    """Rows of cards with a title line."""
    w = max(sum(c.width for c in row) + GAP * (len(row) + 1) for row in cards)
    h = 28 + sum(max(c.height for c in row) + GAP for row in cards) + GAP
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    d.text((GAP, 9), title, fill=INK)
    y = 28
    for row in cards:
        x = GAP
        for c in row:
            im.paste(c, (x, y))
            x += c.width + GAP
        y += max(c.height for c in row) + GAP
    return im


def gate_text(c: dict) -> tuple[str, bool]:
    if c["group"] == "road":
        g = c["hold_gates"]
        return (
            f"road exact {g['road_exact']:.0f}  clash {g['road_clash']}  rim touch {g['rim_touch']}  "
            f"painted {c['painted_share']:.3f}",
            c["passed"],
        )
    g = c["paintin_gates"]
    return (
        f"rim clear {100 * g['rim_clear']:.1f}%  rim touch {g['rim_touch']:.0f}  "
        f"reg {g['registration_residual']:.2f}px",
        c["passed"],
    )


def chunks(items: list, n: int) -> list[list]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def sheets(cands: list[dict], out: Path) -> list[tuple[str, Path]]:
    made: list[tuple[str, Path]] = []
    kept = [c for c in cands if c["kept"]]
    plains = [
        card(SHIPPED / "plains" / f"{i:02d}.png", f"shipped plains/{i:02d}", "", True)
        for i in range(6)
    ]
    for group, title in (("nature", "nature"), ("home", "home")):
        group_cards = [
            card(ROUND / c["tile"], c["label"], *gate_text(c))
            for c in kept
            if c["group"] == group
        ]
        context = (
            plains[:4]
            if group == "nature"
            else plains[:2]
            + [
                card(
                    SHIPPED / "town" / f"{i:02d}.png", f"shipped town/{i:02d}", "", True
                )
                for i in range(2)
            ]
        )
        rows = [context] + chunks(group_cards, 4)
        for i, part in enumerate(chunks(rows, 3)):
            path = out / f"sheet-{group}-{i + 1}.png"
            heading = f"{title}: shipped context (first row), then kept candidates, 3x"
            grid(
                part, heading if i == 0 else f"{title}: kept candidates, 3x (continued)"
            ).save(path)
            made.append((f"{title} {i + 1}", path))
    roads = [c for c in kept if c["group"] == "road"]
    shapes = sorted(
        {c["label"].split("-")[1] for c in roads},
        key=lambda s: (not s.startswith("straight"), s),
    )
    for shape in shapes:
        shipped = sorted((SHIPPED / "road").glob(f"plains_{shape}_*.png"))
        rows = [[card(p, f"shipped {p.stem}", "", True) for p in shipped]]
        mine = [c for c in roads if c["label"].split("-")[1] == shape]
        rows.append(
            [
                card(ROUND / c["tile"], f"{c['label']} (on {c['base']})", *gate_text(c))
                for c in mine
            ]
        )
        if shape in MIRRORED_SHAPES:
            rows.append(
                [
                    card(
                        ROUND / c["tile"],
                        f"{c['label']} drawn mirrored",
                        *gate_text(c),
                        mirror=True,
                    )
                    for c in mine
                ]
            )
        path = out / f"sheet-road-{shape}.png"
        grid(
            rows,
            f"road {shape}: shipped, candidates"
            + (", candidates as drawn mirrored" if shape in MIRRORED_SHAPES else "")
            + ", 3x",
        ).save(path)
        made.append((f"road {shape}", path))
    return made


def short(text: str, n: int = 40) -> str:
    """Card-width text; the full reason is in candidates.json."""
    return text if len(text) <= n else text[: n - 3].rstrip() + "..."


def dropped_sheet(cands: list[dict], out: Path) -> Path:
    cards = []
    for c in cands:
        if c["kept"]:
            continue
        work = REPO / c["work"]
        tile = work / ("held" if c["group"] == "road" else "tiles") / f"{c['cell']}.png"
        why = c.get("why_dropped", "")
        cards.append(
            card(
                tile,
                f"{c['group']} {Path(c['work']).name}:{c['cell']}",
                short(why),
                False,
                scale=2,
            )
        )
    failed = json.loads((ROUND / "candidates.json").read_text())["counts"]
    for group, info in failed.items():
        for key in info["failed_gates"]:
            w, cell = key.split(":")
            work = ROUND / w
            report = json.loads((work / "report.json").read_text())["tiles"][cell]
            bad = [
                k["detail"] for k in report["checks"] if k["hard"] and not k["passed"]
            ]
            cards.append(
                card(
                    work / "tiles" / f"{cell}.png",
                    f"{key} (failed)",
                    short(bad[0]),
                    False,
                    scale=2,
                )
            )
    path = out / "dropped-2x.png"
    grid(
        chunks(cards, 5),
        "not kept (gate-passing, with the reason) and gate failures, 2x",
    ).save(path)
    return path


def patches(cands: list[dict], out: Path) -> Path:
    """Each kept nature/home tile at 64 px in a ring of shipped plains."""
    kept = [c for c in cands if c["kept"] and c["group"] in ("nature", "home")]
    px = 64
    ring = [(0, 0)] + [tuple(d) for d in BM.AXIAL_DIRECTIONS]
    centres = [BM.axial_to_pixel(q, r, px / 2) for q, r in ring]
    span_x = max(x for x, _ in centres) - min(x for x, _ in centres) + px
    span_y = max(y for _, y in centres) - min(y for _, y in centres) + px
    cell_w, cell_h = int(span_x) + 8, int(span_y) + 22
    cols = 8
    rows = (len(kept) + cols - 1) // cols
    im = Image.new("RGB", (cols * cell_w + 8, rows * cell_h + 8), BG)
    d = ImageDraw.Draw(im)
    plains = [
        Image.open(SHIPPED / "plains" / f"{i:02d}.png")
        .convert("RGBA")
        .resize((px, px), Image.LANCZOS)
        for i in range(6)
    ]
    for i, c in enumerate(kept):
        ox = 8 + (i % cols) * cell_w
        oy = 8 + (i // cols) * cell_h
        layer = Image.new("RGBA", (cell_w, cell_h), BG + (255,))
        for j, ((q, r), (x, y)) in enumerate(zip(ring, centres)):
            t = (
                Image.open(ROUND / c["tile"])
                .convert("RGBA")
                .resize((px, px), Image.LANCZOS)
                if j == 0
                else plains[BM.variant_index(q + 7 * i, r, 6)]
            )
            layer.alpha_composite(
                t,
                (
                    round(x - min(cx for cx, _ in centres)),
                    round(14 + y - min(cy for _, cy in centres)),
                ),
            )
        im.paste(layer.convert("RGB"), (ox, oy))
        d.text((ox + 2, oy + 1), c["label"], fill=INK)
    path = out / "patches-64.png"
    im.save(path)
    return path


def picks_sheet(cands: list[dict], picks: list[str], out: Path) -> Path:
    by = {c["label"]: c for c in cands if c["kept"]}
    cards = [
        card(ROUND / by[label]["tile"], label, "", True, scale=2) for label in picks
    ]
    path = out / "picks-2x.png"
    grid(chunks(cards, 5), "first-pass picks, 2x").save(path)
    return path


# ------------------------------------------------------------------- page ----


def insert_notes(page: Path, notes: dict[str, list[str]]) -> None:
    doc = page.read_text(encoding="utf-8")
    for section, paras in notes.items():
        head = f"<h2>{html.escape(section, quote=True)}</h2>"
        if doc.count(head) != 1:
            raise SystemExit(f"section heading not found exactly once: {section!r}")
        body = "\n".join(f'<p class="intro">{html.escape(p)}</p>' for p in paras)
        doc = doc.replace(head, f"{head}\n{body}")
    page.write_text(doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--page", type=Path, required=True)
    ap.add_argument(
        "--notes",
        type=Path,
        required=True,
        help="JSON {section: [paragraph, ...], '_intro': str}",
    )
    args = ap.parse_args()
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    cands = json.loads((ROUND / "candidates.json").read_text())["candidates"]

    board_imgs: list[tuple[str, Path]] = []
    for window in WINDOWS:
        for density in (None, *DENSITIES):
            _, image, report = render(window, density, 64)
            name = density or "before"
            path = out / f"board-{window}-{name}.png"
            image.save(path)
            n = len(report.variety)
            caption = f"{window} window, {'shipped tiles (before)' if density is None else f'variety {density}: {n} decorated hexes'}, 64 px per hex"
            board_imgs.append((caption, path))
    zoom: list[tuple[str, Path]] = []
    for density in (None, "medium"):
        _, image, _ = render("town", density, 128, ZOOM_CROP)
        path = out / f"zoom-town-{density or 'before'}.png"
        image.save(path)
        zoom.append(
            (
                f"town junction at 128 px per hex, {'before' if density is None else 'variety medium'}",
                path,
            )
        )
    units = []
    for px, crop in ((128, ZOOM_CROP), (64, None)):
        path = out / f"units-{px}.png"
        with_units("town", px, crop).save(path)
        units.append(
            (f"unit sprites on decorated hexes, variety medium, {px} px per hex", path)
        )

    sheet_imgs = sheets(cands, out)
    patch = patches(cands, out)
    dropped = dropped_sheet(cands, out)

    notes = json.loads(args.notes.read_text())
    cmd = [
        sys.executable,
        str(REPO / "scripts" / "review-page.py"),
        "--title",
        "Variety round 1",
        "--out",
        str(args.page),
        "--intro",
        notes["_intro"],
        "--png-max-kb",
        "150",
        "--section",
        "1. Board scale: before and after",
        *[f"{c}={p}" for c, p in board_imgs + zoom + units],
        "--section",
        "2. Candidates at 3x with gate results",
        *[f"{c}={p}" for c, p in sheet_imgs],
        f"every kept nature and home tile at 64 px in shipped plains={patch}",
        f"not kept={dropped}",
        "--section",
        "3. Notes",
        f"first-pass picks, 2x={picks_sheet(cands, notes['_picks'], out)}",
    ]
    subprocess.run(cmd, check=True)
    insert_notes(args.page, {k: v for k, v in notes.items() if not k.startswith("_")})
    print(f"{args.page}: {args.page.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
