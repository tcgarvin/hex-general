#!/usr/bin/env python3
"""Review renders and page for variety round 2 (medium-footprint candidates).

Writes into `--out-dir` and bundles into `--page` (`scripts/review-page.py`):

  1. board-<win>-<state>.png  the two real windows (`scripts/board-mock-maps/
     variety-{open,town}.txt`) at 64 px per hex in four states: shipped, round 1
     small features at medium density, round 2 medium features at medium
     density, and a mix (`--mix`, default mostly-medium) at medium density; plus the
     town window in the other mixes (mostly-small, even, mostly-medium)
  2. zoom-town-<state>.png    the town junction at 128 px per hex, same four
  3. units-{64,128}.png       unit sprites on medium-feature hexes (composited
     as in `variety1_review.with_units`)
  4. sheet-*.png              round 2 kept tiles at 3x with gates and occupied
     share, next to the shipped tiles and the round 1 picks they size up
  5. notes (`--notes` JSON) and a picks sheet

usage: python3 art/codex/terrain/variety2_review.py --out-dir <dir> --page <html> \\
    --notes art/codex/terrain/candidates/variety-r2/review_notes.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import variety1_review as r1  # noqa: E402

BM = r1.BM
REPO = r1.REPO
ROUND = HERE / "candidates" / "variety-r2"
SMALL = HERE / "candidates" / "variety-r1" / "kept"
MEDIUM = ROUND / "kept"
SHIPPED = r1.SHIPPED
# shapes the frontend also draws L-R mirrored (riverTiles.ts tables)
MIRRORED_SHAPES = set(r1.MIRRORED_SHAPES) | {
    "fork_se_n_s",
    "fork_ne_n_s",
    "fork_se_ne_nw",
    "fork_se_n_nw",
    "fork_se_nw_sw",
    "fork_se_nw_s",
}
# state -> (small set?, medium set?, mix)
STATES: dict[str, tuple[bool, bool, str]] = {
    "shipped": (False, False, "small"),
    "round1-small": (True, False, "small"),
    "round2-medium": (True, True, "medium"),
}


def render(window: str, state: tuple[bool, bool, str] | None, hex_px: int, crop=None):
    game_map = BM.load_map(r1.MAPS / f"variety-{window}.txt")
    library = BM.TileLibrary(BM.TileSet.discover(SHIPPED, "tiles"), None)
    small, medium, mix = state or (False, False, "small")
    image, report = BM.render(
        game_map,
        library,
        hex_px,
        False,
        crop,
        joins=BM.JoinSet.load(r1.JOINS),
        draw_vector_roads=False,
        sites=BM.SiteSet.discover(r1.SITES),
        variety=BM.VarietySet.discover(SMALL) if small else None,
        variety_density="medium",
        variety_medium=BM.VarietySet.discover(MEDIUM) if medium else None,
        variety_mix=mix,
    )
    return game_map, image.convert("RGB"), report


def caption(window: str, state: str, report, px: int) -> str:
    sizes = {"small": 0, "medium": 0}
    for _, p in report.variety:
        sizes[p.size] += 1
    what = {
        "shipped": "shipped tiles (before)",
        "round1-small": "round 1 small features, medium density",
        "round2-medium": "round 2 medium features, medium density",
    }.get(state, f"mix {state.removeprefix('mix-')}, medium density")
    n = len(report.variety)
    tail = "" if state == "shipped" else f": {n} decorated ({sizes['medium']} medium)"
    return f"{window} window, {what}{tail}, {px} px per hex"


def with_units(hex_px: int, crop) -> Image.Image:
    game_map, image, report = render("town", STATES["round2-medium"], hex_px, crop)
    centres = r1.screen_centres(game_map, hex_px, crop)
    decorated = sorted(
        ((h.q, h.r), p.kind) for h, p in report.variety if (h.q, h.r) in centres
    )
    order = []
    for kind, n in (("home", 3), ("road", 3), ("nature", 3)):
        of_kind = [d for d in decorated if d[1] == kind]
        order += of_kind[:: max(1, len(of_kind) // n)][:n]
    kinds = ("infantry", "armor", "artillery")
    width = round(1.5 * BM.HEX_SIZE * hex_px / (2 * BM.HEX_SIZE))
    out = image.convert("RGBA")
    for i, (at, _) in enumerate(order):
        x, y = centres[at]
        sprite = r1.unit_sprite(
            kinds[i % 3], r1.OWNER_COLOURS[i % 2], width, mirror=bool(i % 2)
        )
        out.alpha_composite(sprite, (round(x - width / 2), round(y - width / 2)))
    return out.convert("RGB")


def gate_text(c: dict) -> tuple[str, bool]:
    text, ok = r1.gate_text(c)
    if "occupied" in c:
        text = f"occupied {c['occupied']:.2f}  " + text.replace("  reg", " reg")
    return text, ok


def sheets(cands: list[dict], out: Path) -> list[tuple[str, Path]]:
    made: list[tuple[str, Path]] = []
    kept = [c for c in cands if c["kept"]]
    small = {"nature": ["nature-01", "nature-12"], "home": ["home-03", "home-06"]}
    for group in ("nature", "home"):
        context = [
            r1.card(SHIPPED / "plains" / "00.png", "shipped plains/00", "", True)
        ] + [
            r1.card(SMALL / group / f"{lab}.png", f"round 1 {lab} (small)", "", True)
            for lab in small[group]
        ]
        if group == "home":
            context.append(
                r1.card(SHIPPED / "town" / "00.png", "shipped town/00", "", True)
            )
        cards = [
            r1.card(ROUND / c["tile"], c["label"], *gate_text(c))
            for c in kept
            if c["group"] == group
        ]
        rows = [context] + r1.chunks(cards, 4)
        for i, part in enumerate(r1.chunks(rows, 3)):
            path = out / f"sheet-{group}-{i + 1}.png"
            head = (
                f"{group}: shipped and round 1 context (first row), then round 2 kept, 3x"
                if i == 0
                else f"{group}: round 2 kept, 3x (continued)"
            )
            r1.grid(part, head).save(path)
            made.append((f"{group} {i + 1}", path))
    roads = [c for c in kept if c["group"] == "road"]
    groups: dict[str, list[dict]] = {}
    for c in roads:
        shape = c["label"].split("-")[1]
        groups.setdefault("forks" if shape.startswith("fork") else shape, []).append(c)
    for key in sorted(
        groups, key=lambda k: (k == "forks", not k.startswith("straight"), k)
    ):
        mine = groups[key]
        rows = []
        if key != "forks":
            shipped = sorted((SHIPPED / "road").glob(f"plains_{key}_*.png"))[:3]
            rows.append([r1.card(p, f"shipped {p.stem}", "", True) for p in shipped])
            rows.append(
                [r1.card(ROUND / c["tile"], c["label"], *gate_text(c)) for c in mine]
            )
            if key in MIRRORED_SHAPES:
                rows.append(
                    [
                        r1.card(
                            ROUND / c["tile"],
                            f"{c['label']} mirrored",
                            *gate_text(c),
                            mirror=True,
                        )
                        for c in mine
                    ]
                )
            parts = [rows]
        else:
            cards = []
            for c in mine:
                shape = c["label"].split("-")[1]
                cards.append(
                    r1.card(
                        SHIPPED / "road" / f"plains_{shape}_00.png",
                        f"shipped {shape}",
                        "",
                        True,
                    )
                )
                cards.append(r1.card(ROUND / c["tile"], c["label"], *gate_text(c)))
            parts = r1.chunks(r1.chunks(cards, 4), 2)
        for i, part in enumerate(parts):
            path = out / f"sheet-road-{key}-{i + 1}.png"
            r1.grid(
                part,
                f"road {key}: shipped and round 2, 3x"
                + (" (with mirrored copies)" if key in MIRRORED_SHAPES else ""),
            ).save(path)
            made.append((f"road {key} {i + 1}", path))
    return made


def dropped_sheet(cands: list[dict], out: Path) -> Path:
    cards = []
    for c in cands:
        if c["kept"]:
            continue
        work = REPO / c["work"]
        tile = work / ("held" if c["group"] == "road" else "tiles") / f"{c['cell']}.png"
        cards.append(
            r1.card(
                tile,
                f"{Path(c['work']).name}:{c['cell']}",
                r1.short(c["why_dropped"]),
                False,
                scale=2,
            )
        )
    counts = json.loads((ROUND / "candidates.json").read_text())["counts"]
    for group, info in counts.items():
        for key in info["failed_gates"]:
            w, cell = key.split(":")
            work = ROUND / w
            if group == "road":
                h = json.loads((work / "hold.json").read_text())["tiles"][cell][
                    "checks"
                ]
                why = f"road clash {h['road_clash']} (limit 12)"
                tile = work / "held" / f"{cell}.png"
            else:
                report = json.loads((work / "report.json").read_text())["tiles"][cell]
                why = [
                    k["detail"]
                    for k in report["checks"]
                    if k["hard"] and not k["passed"]
                ][0]
                tile = work / "tiles" / f"{cell}.png"
            cards.append(
                r1.card(tile, f"{key} (failed)", r1.short(why), False, scale=2)
            )
    path = out / "dropped-2x.png"
    r1.grid(
        r1.chunks(cards, 5), "not kept (with the reason) and gate failures, 2x"
    ).save(path)
    return path


def picks_sheet(cands: list[dict], picks: list[str], out: Path) -> Path:
    by = {c["label"]: c for c in cands if c["kept"]}
    cards = [r1.card(ROUND / by[lab]["tile"], lab, "", True, scale=2) for lab in picks]
    path = out / "picks-2x.png"
    r1.grid(r1.chunks(cards, 5), "first-pass picks, round 2, 2x").save(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--page", type=Path, required=True)
    ap.add_argument("--notes", type=Path, required=True)
    ap.add_argument("--mix", default="mostly-medium", choices=tuple(BM.VARIETY_MIXES))
    args = ap.parse_args()
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    cands = json.loads((ROUND / "candidates.json").read_text())["candidates"]
    states = dict(STATES)
    states[f"mix-{args.mix}"] = (True, True, args.mix)

    boards, zooms = [], []
    for window in r1.WINDOWS:
        for name, st in states.items():
            _, image, report = render(window, st, 64)
            path = out / f"board-{window}-{name}.png"
            image.save(path)
            boards.append((caption(window, name, report, 64), path))
    for mix in ("mostly-small", "even", "mostly-medium"):
        if mix == args.mix:
            continue
        _, image, report = render("town", (True, True, mix), 64)
        path = out / f"board-town-mix-{mix}.png"
        image.save(path)
        boards.append((caption("town", f"mix-{mix}", report, 64), path))
    for name, st in states.items():
        _, image, report = render("town", st, 128, r1.ZOOM_CROP)
        path = out / f"zoom-town-{name}.png"
        image.save(path)
        zooms.append((caption("town junction", name, report, 128), path))
    units = []
    for px, crop in ((64, None), (128, r1.ZOOM_CROP)):
        path = out / f"units-{px}.png"
        with_units(px, crop).save(path)
        units.append((f"units on round 2 medium features, {px} px per hex", path))

    notes = json.loads(args.notes.read_text())
    cmd = [
        sys.executable,
        str(REPO / "scripts" / "review-page.py"),
        "--title",
        "Variety round 2",
        "--out",
        str(args.page),
        "--intro",
        notes["_intro"],
        "--png-max-kb",
        "150",
        "--section",
        "1. Board scale at 64 px per hex",
        *[f"{c}={p}" for c, p in boards],
        "--section",
        "2. Town junction at 128 px per hex",
        *[f"{c}={p}" for c, p in zooms],
        "--section",
        "3. Units on medium features",
        *[f"{c}={p}" for c, p in units],
        "--section",
        "4. Round 2 candidates at 3x with gate results",
        *[f"{c}={p}" for c, p in sheets(cands, out)],
        f"not kept={dropped_sheet(cands, out)}",
        "--section",
        "5. Notes",
        f"first-pass picks={picks_sheet(cands, notes['_picks'], out)}",
    ]
    subprocess.run(cmd, check=True)
    r1.insert_notes(
        args.page, {k: v for k, v in notes.items() if not k.startswith("_")}
    )
    print(f"{args.page}: {args.page.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
