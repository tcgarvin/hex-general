#!/usr/bin/env python3
"""Paint-in tile toolchain: Codex paints a feature in context on our own ground,
then every tile is pinned back to that ground so any tile abuts any other.

Proven by the 2026-11-09 Codex terrain probe (art/codex/terrain/README.md, P5):
Codex keeps a magenta sheet's hexagon silhouettes to ~1 px and paints features
in register, but re-renders the ground it was given. This tool turns that into
a dependable loop:

  sheet    job.json -> <name>.sheet.png + <name>.sheet.json (the sidecar every
           later stage reads): separated flat-top hexagons on flat #FF00FF,
           each a base tile, optionally with a deterministic guide (river band /
           road ribbon through named edge midpoints, bridge-deck marker).
  cut      Codex output -> registration.json + cut/<cell>.png: fit isotropic
           scale + translation from the hexagon silhouettes, fail loudly on a
           missing cell or a residual / width / IoU over its limit, resample
           each cell to 128 px.
  profile  extract a canonical edge cross-section from a painted cut cell (the
           job can also extract it inside `pin`/`run`, or point at a saved one).
  pin      cut + base (+ guide + profile) -> tiles/<cell>.png: rim pinned to the
           base exactly, crossings pinned to the shared profile, optional
           difference-matte ground restore (`--restore`).
  gate     per-tile checks with measured values -> report.json (exit 1 on any
           hard failure).
  run      all of the above, plus the Codex call (`docs/ART_PIPELINE.md`, "Codex
           recipe"), a labelled montage, and a provenance entry in the
           manifest. `--from-raw` replays from a saved raw without calling Codex.
  preview  adjacency preview of oriented tiles along a hex course, at 128 and
           64 px per hex.
  adopt    wrap a hand-built probe sheet (art/codex/terrain/refs/paintin_sheet.*)
           as a sidecar + base tiles so its Codex outputs can be cut and pinned.

Typical use (a new class of tiles):

    python3 scripts/tile-foundry/paintin.py run --job <job.json> \\
        --prompt <prompt.txt> --ref docs/concept-art/concept1.png \\
        --out-dir art/codex/terrain/<set> --review-dir art/codex/terrain/review
    # deterministic replay from the saved raw (no Codex call):
    python3 scripts/tile-foundry/paintin.py run --job <job.json> \\
        --prompt <prompt.txt> --out-dir art/codex/terrain/<set> \\
        --from-raw art/codex/terrain/<set>/raw/<name>.png

Job file: see `paintin_sheet.load_job`; worked examples live next to the proof
outputs in art/codex/terrain/toolproof/. Dependencies: numpy + Pillow only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from hexgeom import AXIAL_DIRECTIONS, axial_to_pixel  # noqa: E402
from paintin_gate import (  # noqa: E402
    GateCheck,
    GateConfig,
    checks_to_json,
    gate_cell,
    luminance,
    tile_colour_stats,
)
from paintin_geom import TILE_PX, tile_distance  # noqa: E402
from paintin_pin import (  # noqa: E402
    CrossProfile,
    PinConfig,
    extract_profile,
    feature_mask,
    measure_crossing,
    normalise_feature,
    pin_tile,
)
from paintin_sheet import (  # noqa: E402
    MAGENTA,
    BaseTile,
    CellFit,
    JobError,
    Registration,
    build_sheet,
    cell_guide,
    cut_cell,
    load_base,
    load_job,
    load_sidecar,
    output_alpha,
    register,
    repo_path,
    repo_rel,
    sha256_of,
)
from river import ORIENTED_TILES  # noqa: E402

CODEX_TIMEOUT_S = 900
CODEX_IMAGES = Path.home() / ".codex" / "generated_images"


# ------------------------------------------------------------------ codex ----


@dataclass(frozen=True)
class CodexRun:
    """Provenance of one Codex image-generation call."""

    thread_id: str
    source: Path
    wall_seconds: int
    refs: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        """The `<raw>.run.json` record (same fields as run_codex.sh)."""
        return {
            "codex_thread": self.thread_id,
            "codex_source": str(self.source),
            "wall_seconds": self.wall_seconds,
            "refs": list(self.refs),
        }


def run_codex(prompt: Path, images: list[Path], dest: Path) -> CodexRun:
    """One Codex call per docs/ART_PIPELINE.md "Codex recipe"; copies the image to `dest`."""
    with tempfile.TemporaryDirectory(prefix="paintin-codex-") as wd:
        cmd = ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", "-C", wd]
        for img in images:
            cmd += ["-i", str(img.resolve())]
        cmd += ["--json", "-"]
        start = time.time()
        with prompt.open("rb") as stdin:
            proc = subprocess.run(
                cmd,
                stdin=stdin,
                capture_output=True,
                cwd=wd,
                timeout=CODEX_TIMEOUT_S,
                check=False,
            )
        wall = round(time.time() - start)
    if proc.returncode != 0:
        raise RuntimeError(
            f"codex exec exited {proc.returncode}: {proc.stderr.decode()[-2000:]}"
        )
    lines = proc.stdout.decode().splitlines()
    if not lines:
        raise RuntimeError("codex exec produced no JSONL events")
    first = json.loads(lines[0])
    thread_id = first.get("thread_id")
    if not isinstance(thread_id, str):
        raise RuntimeError(f"first codex event has no thread_id: {lines[0][:300]}")
    produced = sorted(
        (CODEX_IMAGES / thread_id).glob("*.png"), key=lambda p: p.stat().st_mtime
    )
    if not produced:
        raise RuntimeError(
            f"codex thread {thread_id} produced no image under {CODEX_IMAGES / thread_id}; "
            f"last events: {lines[-3:]}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(produced[-1], dest)
    run = CodexRun(
        thread_id=thread_id,
        source=produced[-1],
        wall_seconds=wall,
        refs=tuple(str(i.resolve()) for i in images),
    )
    dest.with_suffix(".run.json").write_text(json.dumps(run.to_json(), indent=1))
    return run


# --------------------------------------------------------------- pipeline ----


def load_rgb(path: Path) -> np.ndarray:
    """Load an image as float RGB, failing with context."""
    try:
        img = Image.open(path)
        img.load()
    except OSError as err:
        raise JobError(f"{path}: cannot read image: {err}") from err
    return np.asarray(img.convert("RGB"), dtype=np.float64)


def save_rgb(arr: np.ndarray, path: Path) -> None:
    """Save a float RGB array as an 8-bit PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(np.round(arr), 0, 255).astype(np.uint8)).save(path)


def bases_for(sidecar: dict[str, Any]) -> dict[str, BaseTile]:
    """Load every cell's base tile, checking it is the one the sheet was built from."""
    bases = {}
    for cell in sidecar["cells"]:
        path = repo_path(cell["base"])
        if sha256_of(path) != cell["base_sha256"]:
            raise JobError(f"{path}: base tile changed since the sheet was built")
        bases[cell["name"]] = load_base(path)
    return bases


def stage_cut(
    sidecar: dict[str, Any], raw: Path, work: Path, gates: GateConfig
) -> tuple[Registration, dict[str, np.ndarray]]:
    """Register `raw` to the sheet, write cut/<cell>.png and registration.json."""
    out_rgb = load_rgb(raw)
    reg = register(
        out_rgb,
        sidecar,
        max_residual_tile_px=gates.max_residual_tile_px,
        min_iou=gates.min_iou,
    )
    bases = bases_for(sidecar)
    cuts = {}
    for cell in sidecar["cells"]:
        cut = cut_cell(out_rgb, reg, cell, bases[cell["name"]])
        save_rgb(cut, work / "cut" / cell["output"])
        cuts[cell["name"]] = cut
    (work / "registration.json").write_text(
        json.dumps({"raw": repo_rel(raw), **reg.to_json()}, indent=1)
    )
    return reg, cuts


def load_cuts(sidecar: dict[str, Any], cut_dir: Path) -> dict[str, np.ndarray]:
    """Read cut/<cell>.png for every cell."""
    return {c["name"]: load_rgb(cut_dir / c["output"]) for c in sidecar["cells"]}


def resolve_profile(
    sidecar: dict[str, Any],
    cuts: dict[str, np.ndarray],
    bases: dict[str, BaseTile],
    work: Path,
) -> CrossProfile | None:
    """The job's cross-section profile: loaded, or extracted and saved to work/.

    None when the job has no oriented cells (and so names no profile).
    """
    spec = sidecar["profile"]
    if "path" in spec:
        return CrossProfile.load(Path(spec["path"]))
    if "extract" not in spec:
        return None
    ex = spec["extract"]
    cell = next(c for c in sidecar["cells"] if c["name"] == ex["cell"])
    guide = cell_guide(cell)
    if guide is None:
        raise JobError(f"profile.extract.cell {ex['cell']!r} has no guide")
    edges = tuple(ex.get("edges", guide.edges))
    profile = extract_profile(
        cuts[cell["name"]],
        bases[cell["name"]].tile,
        guide.kind,
        edges,
        source={"sheet": sidecar["sheet"], "cell": cell["name"]},
        bank_px=float(spec.get("bank_px", 3.0)),
        feather_px=float(spec.get("feather_px", 4.0)),
    )
    profile.save(work / "profile.json")
    return profile


@dataclass
class TileOutcome:
    """Everything produced for one cell."""

    name: str
    base: np.ndarray
    cut: np.ndarray
    final: np.ndarray  # uint8 RGBA
    matte: np.ndarray
    checks: list[GateCheck]

    @property
    def passed(self) -> bool:
        """True when every hard check passed."""
        return all(c.passed or not c.hard for c in self.checks)


def stage_pin_gate(
    sidecar: dict[str, Any],
    reg_cells: dict[str, CellFit],
    cuts: dict[str, np.ndarray],
    work: Path,
    tiles_dir: Path,
    pin_cfg: PinConfig,
    gate_cfg: GateConfig,
) -> list[TileOutcome]:
    """Pin every cell, write tiles_dir/<cell>.png, gate each tile.

    With `feature_strength` set, each cut's feature colour is normalised first
    (`normalise_feature`, recorded in work/normalise.json), so the profile, the
    pin and the gates all see the normalised painting.
    """
    bases = bases_for(sidecar)
    shifts = {}
    normalised = {}
    for cell in sidecar["cells"]:
        name = cell["name"]
        normalised[name], shifts[name] = normalise_feature(
            cuts[name], bases[name].tile, cell_guide(cell), pin_cfg
        )
    cuts = normalised
    if pin_cfg.feature_strength > 0:
        (work / "normalise.json").write_text(json.dumps(shifts, indent=1))
    profile = resolve_profile(sidecar, cuts, bases, work)
    alpha = output_alpha()
    tiles_dir.mkdir(parents=True, exist_ok=True)
    outcomes = []
    for cell in sidecar["cells"]:
        name = cell["name"]
        guide = cell_guide(cell)
        base = bases[name].tile
        result = pin_tile(cuts[name], base, alpha, guide, profile, pin_cfg)
        Image.fromarray(result.rgba, "RGBA").save(tiles_dir / cell["output"])
        checks = gate_cell(
            reg_cells[name],
            cuts[name],
            base,
            result.rgba,
            result.target,
            guide,
            profile if guide is not None else None,
            pin_cfg,
            gate_cfg,
            {**sidecar["anchors"], **cell.get("anchors", {})},
        )
        outcomes.append(
            TileOutcome(name, base, cuts[name], result.rgba, result.matte, checks)
        )
    return outcomes


def reg_cells_from_json(path: Path) -> dict[str, CellFit]:
    """CellFit records from a registration.json."""
    raw = json.loads(path.read_text())
    return {
        k: CellFit(
            name=v["name"],
            expected=tuple(v["expected"]),  # type: ignore[arg-type]
            measured=tuple(v["measured"]),  # type: ignore[arg-type]
            width_ratio=v["width_ratio"],
            residual_px=v["residual_px"],
            residual_tile_px=v["residual_tile_px"],
            iou=v["iou"],
        )
        for k, v in raw["cells"].items()
    }


def crossing_report(
    sidecar: dict[str, Any], cuts: dict[str, np.ndarray], bases: dict[str, BaseTile]
) -> dict[str, Any]:
    """How closely Codex followed each guide before pinning (vs the guide itself).

    Per connected edge: crossing centre offset and width at depths 2-6 px and
    at the pin hand-over depth 10-16 px, from the feature classifier.
    """
    out: dict[str, Any] = {}
    for cell in sidecar["cells"]:
        guide = cell_guide(cell)
        if guide is None:
            continue
        mask = feature_mask(guide.kind, cuts[cell["name"]], bases[cell["name"]].tile)
        per_edge = {}
        for edge in guide.edges:
            row = {}
            for label, lo, hi in (("edge", 2.0, 6.0), ("handover", 10.0, 16.0)):
                c = measure_crossing(mask, edge, lo, hi)
                row[label] = (
                    "no crossing"
                    if c is None
                    else {"centre": round(c.centre, 2), "width": round(c.width, 2)}
                )
            per_edge[str(edge)] = row
        out[cell["name"]] = {"guide_width": guide.width, "edges": per_edge}
    return out


def write_report(
    work: Path,
    sidecar: dict[str, Any],
    reg: dict[str, Any],
    outcomes: list[TileOutcome],
    pin_cfg: PinConfig,
    extra: dict[str, Any],
    name: str = "report.json",
) -> Path:
    """report.json: registration summary, pin settings, per-tile gates + colour stats."""
    report = {
        "sheet": sidecar["sheet"],
        "registration": {
            k: reg[k] for k in ("scale", "tx", "ty", "max_residual_tile_px", "min_iou")
        },
        "pin": pin_cfg.to_json(),
        "tiles": {
            o.name: {
                "passed": o.passed,
                "colour": tile_colour_stats(o.final),
                "checks": checks_to_json(o.checks),
            }
            for o in outcomes
        },
        "passed": all(o.passed for o in outcomes),
        **extra,
    }
    path = work / name
    path.write_text(json.dumps(report, indent=1, default=float))
    return path


def print_outcomes(outcomes: list[TileOutcome]) -> None:
    """Per-tile verdict with every failing / advisory check."""
    for o in outcomes:
        print(f"{'PASS' if o.passed else 'FAIL'} {o.name}")
        for c in o.checks:
            if not c.passed:
                print(f"    {c.line()}")


# ---------------------------------------------------------------- review ----

CHECKER = ((70, 70, 78), (96, 96, 104))
BG = (34, 34, 40)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def tile_on_checker(rgba: np.ndarray, scale: int) -> Image.Image:
    """A tile over a checkerboard, nearest-free (LANCZOS) scaled by `scale`."""
    img = Image.fromarray(rgba, "RGBA" if rgba.shape[-1] == 4 else "RGB").convert(
        "RGBA"
    )
    size = img.width * scale
    img = img.resize((size, size), Image.Resampling.LANCZOS) if scale != 1 else img
    bg = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(bg)
    step = 8 * scale
    for y in range(0, size, step):
        for x in range(0, size, step):
            d.rectangle(
                [x, y, x + step - 1, y + step - 1],
                fill=CHECKER[((x // step) + (y // step)) % 2],
            )
    bg.alpha_composite(img)
    return bg.convert("RGB")


def montage(
    rows: list[tuple[str, list[tuple[str, np.ndarray]]]],
    scale: int,
    out: Path,
    title: str,
) -> Path:
    """Labelled grid: one row per tile, one column per stage (label above each)."""
    cols = max(len(r[1]) for r in rows)
    cell = TILE_PX * scale
    pad, label_h, row_label_w, title_h = 8, 18, 170, 30
    w = row_label_w + cols * (cell + pad) + pad
    h = title_h + len(rows) * (cell + label_h + pad) + pad
    canvas = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((pad, 6), title, fill=(240, 240, 240), font=_font(16))
    for i, (row_label, items) in enumerate(rows):
        y = title_h + i * (cell + label_h + pad)
        draw.text(
            (pad, y + label_h + cell // 2 - 8),
            row_label,
            fill=(255, 220, 90),
            font=_font(13),
        )
        for j, (label, arr) in enumerate(items):
            x = row_label_w + j * (cell + pad)
            draw.text((x, y), label, fill=(220, 220, 220), font=_font(12))
            canvas.paste(tile_on_checker(arr, scale), (x, y + label_h))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def as_rgba(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Float RGB + uint8 alpha -> uint8 RGBA."""
    out = np.empty((*rgb.shape[:2], 4), dtype=np.uint8)
    out[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    out[..., 3] = alpha
    return out


def matte_rgba(matte: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Greyscale view of a 0..1 matte."""
    g = np.clip(matte * 255.0, 0, 255)
    return as_rgba(np.stack([g, g, g], axis=-1), alpha)


# --------------------------------------------------------------- preview ----


def board_preview(
    placements: list[tuple[int, int, np.ndarray, bool]], out_128: Path, out_64: Path
) -> None:
    """Place 128 px tiles as board neighbours (axial q, r; optional L-R mirror).

    Writes the board at 128 px per hex and a LANCZOS half-size copy (64 px per
    hex, the in-game zoom-1 footprint).
    """
    centres = [axial_to_pixel(q, r, 64.0) for q, r, _, _ in placements]
    xs = [c[0] for c in centres]
    ys = [c[1] for c in centres]
    pad = 16
    x0, y0 = min(xs) - 64 - pad, min(ys) - 64 - pad
    w = int(max(xs) - x0 + 64 + pad)
    h = int(max(ys) - y0 + 64 + pad)
    canvas = Image.new("RGBA", (w, h), BG + (255,))
    for (q, r, rgba, mirror), (cx, cy) in zip(placements, centres):
        tile = Image.fromarray(rgba, "RGBA")
        if mirror:
            tile = tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        canvas.alpha_composite(tile, (round(cx - x0 - 64), round(cy - y0 - 64)))
    rgb = canvas.convert("RGB")
    out_128.parent.mkdir(parents=True, exist_ok=True)
    rgb.save(out_128)
    rgb.resize((w // 2, h // 2), Image.Resampling.LANCZOS).save(out_64)


def parse_course(spec: str) -> dict[tuple[int, int], tuple[int, ...]]:
    """Parse "q,r:edges;..." (edges as digits, e.g. "0,2:25") and check that every
    connection is reciprocal (the neighbour across edge e lists edge e + 3)."""
    course: dict[tuple[int, int], tuple[int, ...]] = {}
    for item in spec.split(";"):
        try:
            coord, edge_digits = item.split(":")
            q, r = (int(v) for v in coord.split(","))
            course[(q, r)] = tuple(sorted(int(ch) for ch in edge_digits))
        except ValueError as err:
            raise JobError(f"bad course item {item!r} (want q,r:edges): {err}") from err
    for (q, r), edges in course.items():
        for e in edges:
            dq, dr = AXIAL_DIRECTIONS[e]
            nb = (q + dq, r + dr)
            if (e + 3) % 6 not in course.get(nb, ()):
                raise JobError(
                    f"course hex ({q},{r}) connects edge {e} but {nb} does not "
                    f"connect edge {(e + 3) % 6} back"
                )
    return course


def course_placements(
    course: dict[tuple[int, int], tuple[int, ...]],
    tiles: dict[str, list[np.ndarray]],
    fill: list[np.ndarray],
    ring: int = 1,
) -> list[tuple[int, int, np.ndarray, bool]]:
    """Oriented tiles for a course plus `ring` hexes of fill around it.

    Each hex's connected-edge set picks the shape and mirror flag exactly as
    riverTiles.ts does; variants rotate by position. Edge sets are explicit
    (see `parse_course`) because raw adjacency over-connects at junctions,
    which the frontend prunes (`riverCourseEdges`).
    """
    out = []
    for (q, r), edges in course.items():
        if edges not in ORIENTED_TILES:
            raise ValueError(f"course hex ({q},{r}) has unsupported edge set {edges}")
        shape, mirror = ORIENTED_TILES[edges]
        if shape not in tiles:
            raise ValueError(
                f"course hex ({q},{r}) needs shape {shape!r}; have {sorted(tiles)}"
            )
        variants = tiles[shape]
        out.append((q, r, variants[(q * 7 + r * 13) % len(variants)], mirror))
    members = set(course)
    around: set[tuple[int, int]] = set()
    frontier = set(members)
    for _ in range(ring):
        nxt = set()
        for q, r in frontier:
            for dq, dr in AXIAL_DIRECTIONS:
                h = (q + dq, r + dr)
                if h not in members and h not in around:
                    nxt.add(h)
        around |= nxt
        frontier = nxt
    for k, (q, r) in enumerate(sorted(around)):
        out.append((q, r, fill[k % len(fill)], False))
    return out


# ------------------------------------------------------------------ adopt ----


def run_adopt(args: argparse.Namespace) -> int:
    """Wrap a hand-built probe sheet as a sidecar + 340 px base masters."""
    legacy = json.loads(args.legacy_json.read_text())
    ground = Image.open(args.ground).convert("RGB")
    sheet_img = Image.open(args.sheet)
    if ground.size != sheet_img.size:
        raise JobError(f"ground {ground.size} and sheet {sheet_img.size} sizes differ")
    names = args.names.split(",")
    hexes = legacy["hexes"]
    if len(names) != len(hexes):
        raise JobError(f"{len(names)} names for {len(hexes)} hexes")
    w = int(legacy["hex_width"])
    bases_dir = args.out_dir / "bases"
    bases_dir.mkdir(parents=True, exist_ok=True)
    cells = []
    n = round(len(hexes) ** 0.5)
    for i, (name, hx) in enumerate(zip(names, hexes)):
        cx, cy = float(hx["cx"]), float(hx["cy"])
        x0, y0 = round(cx - w / 2), round(cy - w / 2)
        base_path = bases_dir / f"{args.prefix}_{i}.png"
        ground.crop((x0, y0, x0 + w, y0 + w)).save(base_path)
        cells.append(
            {
                "name": name,
                "slot": i,
                "cx": cx,
                "cy": cy,
                "hex_width": w,
                "scale": w / 128.0,
                "base": repo_rel(base_path),
                "base_sha256": sha256_of(base_path),
                "guide": None,
                "output": f"{name}.png",
            }
        )
    sidecar = {
        "tool": "scripts/tile-foundry/paintin.py (adopt)",
        "version": 1,
        "job": "adopted from " + repo_rel(args.legacy_json),
        "job_sha256": sha256_of(args.legacy_json),
        "name": args.prefix,
        "sheet": repo_rel(args.sheet),
        "sheet_sha256": sha256_of(args.sheet),
        "canvas": list(sheet_img.size),
        "layout": {"canvas": sheet_img.width, "cols": n, "rows": n, "hex_width": w},
        "background": list(MAGENTA),
        "tile_px": TILE_PX,
        "profile": {},
        "pin": {},
        "gates": {},
        "anchors": json.loads(args.anchors) if args.anchors else {},
        "cells": cells,
    }
    out = args.out_dir / f"{args.prefix}.sheet.json"
    out.write_text(json.dumps(sidecar, indent=1))
    print(f"wrote {out} and {len(cells)} base masters in {bases_dir}")
    return 0


# ------------------------------------------------------------- commands ----


def pin_config(sidecar: dict[str, Any], restore: bool | None) -> PinConfig:
    """The sidecar's pin settings, with an optional --restore/--no-restore override."""
    raw = dict(sidecar["pin"])
    if restore is not None:
        raw["restore"] = restore
    return PinConfig.from_json(raw)


def run_sheet(args: argparse.Namespace) -> int:
    """`sheet` subcommand."""
    job = load_job(args.job)
    sheet, side = build_sheet(job, args.out_dir)
    print(f"wrote {sheet} and {side} ({len(job.cells)} cells, layout {job.layout})")
    return 0


def run_cut(args: argparse.Namespace) -> int:
    """`cut` subcommand."""
    sidecar = load_sidecar(args.sidecar)
    gates = GateConfig.from_json(sidecar["gates"])
    reg, _ = stage_cut(sidecar, args.raw, args.out_dir, gates)
    worst = max(c.residual_tile_px for c in reg.cells.values())
    print(
        f"registered: scale {reg.scale:.4f} t=({reg.tx:.2f}, {reg.ty:.2f}); "
        f"max residual {worst:.2f} tile px; cut {len(reg.cells)} cells -> {args.out_dir / 'cut'}"
    )
    return 0


def run_profile(args: argparse.Namespace) -> int:
    """`profile` subcommand."""
    sidecar = load_sidecar(args.sidecar)
    cell = next((c for c in sidecar["cells"] if c["name"] == args.cell), None)
    if cell is None:
        raise JobError(f"no cell {args.cell!r} in {args.sidecar}")
    guide = cell_guide(cell)
    if guide is None:
        raise JobError(f"cell {args.cell!r} has no guide")
    cut = load_rgb(args.cut_dir / cell["output"])
    base = load_base(repo_path(cell["base"])).tile
    edges = tuple(args.edges) if args.edges else guide.edges
    profile = extract_profile(
        cut,
        base,
        guide.kind,
        edges,
        source={"sheet": sidecar["sheet"], "cell": args.cell},
        bank_px=args.bank,
        feather_px=args.feather,
    )
    profile.save(args.out)
    print(
        f"wrote {args.out}: width {profile.feature_width:.1f}, core half "
        f"{profile.core_half:.1f}, asymmetry {profile.source['asymmetry_mean_abs_rgb']}"
    )
    return 0


def run_pin_gate(args: argparse.Namespace) -> int:
    """`pin` and `gate` subcommands (gate re-pins in memory; pin also gates)."""
    sidecar = load_sidecar(args.sidecar)
    pin_cfg = pin_config(sidecar, args.restore)
    gate_cfg = GateConfig.from_json(sidecar["gates"])
    reg_path = args.work_dir / "registration.json"
    cuts = load_cuts(sidecar, args.work_dir / "cut")
    outcomes = stage_pin_gate(
        sidecar,
        reg_cells_from_json(reg_path),
        cuts,
        args.work_dir,
        args.tiles_dir or args.work_dir / "tiles",
        pin_cfg,
        gate_cfg,
    )
    tiles_dir = args.tiles_dir or args.work_dir / "tiles"
    report = write_report(
        args.work_dir,
        sidecar,
        json.loads(reg_path.read_text()),
        outcomes,
        pin_cfg,
        {},
        name=f"report-{tiles_dir.name}.json",
    )
    print_outcomes(outcomes)
    print(f"wrote {report}")
    return 0 if all(o.passed for o in outcomes) else 1


def append_manifest(manifest: Path, entry: dict[str, Any]) -> None:
    """Append one run record to a JSON manifest ({"runs": [...]})."""
    data: dict[str, Any] = {"runs": []}
    if manifest.exists():
        data = json.loads(manifest.read_text())
        if not isinstance(data.get("runs"), list):
            raise JobError(f"{manifest}: manifest has no 'runs' list")
    data["runs"].append(entry)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(data, indent=1))


def run_run(args: argparse.Namespace) -> int:
    """`run` subcommand: sheet -> Codex (or --from-raw) -> cut -> pin -> gate -> review."""
    job = load_job(args.job)
    work = args.out_dir
    sheet_path, sidecar_path = build_sheet(job, work)
    sidecar = load_sidecar(sidecar_path)
    gate_cfg = GateConfig.from_json(sidecar["gates"])
    pin_cfg = pin_config(sidecar, args.restore)
    if args.from_raw is not None:
        raw = args.from_raw
        run_json = raw.with_suffix(".run.json")
        codex = (
            json.loads(run_json.read_text())
            if run_json.exists()
            else {"not_recorded": f"{repo_rel(run_json)} does not exist"}
        )
    else:
        raw = work / "raw" / f"{job.name}.png"
        if raw.exists() and not args.overwrite_raw:
            raise JobError(
                f"{raw} exists; replay it with --from-raw or pass --overwrite-raw"
            )
        refs = [sheet_path, *args.ref]
        print(f"calling Codex with {[repo_rel(r) for r in refs]} ...", flush=True)
        codex = run_codex(args.prompt, refs, raw).to_json()
        print(f"codex thread {codex['codex_thread']}, {codex['wall_seconds']} s")
    reg, cuts = stage_cut(sidecar, raw, work, gate_cfg)
    outcomes = stage_pin_gate(
        sidecar, reg.cells, cuts, work, work / "tiles", pin_cfg, gate_cfg
    )
    bases = bases_for(sidecar)
    follow = crossing_report(sidecar, cuts, bases)
    report = write_report(
        work, sidecar, reg.to_json(), outcomes, pin_cfg, {"codex_follow": follow}
    )
    review_dir = args.review_dir or work / "review"
    alpha = output_alpha()
    rows = [
        (
            f"{o.name}\n{'PASS' if o.passed else 'FAIL'}",
            [
                ("base", as_rgba(o.base, alpha)),
                ("cut (Codex)", as_rgba(o.cut, alpha)),
                ("final", o.final),
            ],
        )
        for o in outcomes
    ]
    mont = montage(
        rows,
        2,
        review_dir / f"{job.name}.montage.png",
        f"{job.name}: base | cut | final",
    )
    print_outcomes(outcomes)
    entry = {
        "name": job.name,
        "when": dt.datetime.now().isoformat(timespec="seconds"),
        "job": repo_rel(job.path),
        "job_sha256": sha256_of(job.path),
        "prompt": repo_rel(args.prompt),
        "prompt_sha256": hashlib.sha256(args.prompt.read_bytes()).hexdigest(),
        "sheet": repo_rel(sheet_path),
        "raw": repo_rel(raw),
        "raw_sha256": sha256_of(raw),
        "raw_size": list(Image.open(raw).size),
        "from_raw": args.from_raw is not None,
        "codex": codex,
        "refs": [repo_rel(sheet_path)] + [repo_rel(r) for r in args.ref],
        "registration": {
            k: reg.to_json()[k]
            for k in ("scale", "tx", "ty", "max_residual_tile_px", "min_iou")
        },
        "pin": pin_cfg.to_json(),
        "gates": sidecar["gates"],
        "report": repo_rel(report),
        "montage": repo_rel(mont),
        "passed": [o.name for o in outcomes if o.passed],
        "failed": [o.name for o in outcomes if not o.passed],
    }
    append_manifest(args.manifest or work / "manifest.json", entry)
    print(f"wrote {report}, {mont}")
    return 0 if all(o.passed for o in outcomes) else 1


def run_preview(args: argparse.Namespace) -> int:
    """`preview` subcommand: oriented tiles placed along a course, 128 + 64 px."""
    shapes = sorted({s for s, _ in ORIENTED_TILES.values()} | {"straight_v"}, key=len)
    tiles: dict[str, list[np.ndarray]] = {}
    for path in sorted(args.tiles_dir.glob("*.png")):
        matches = [s for s in shapes if path.stem == s or path.stem.startswith(s + "_")]
        if matches:
            tile = np.asarray(Image.open(path).convert("RGBA"))
            tiles.setdefault(matches[-1], []).append(tile)
    alpha = output_alpha()
    fill = [as_rgba(load_base(p).tile, alpha) for p in args.fill]
    placements = course_placements(parse_course(args.course), tiles, fill)
    p128 = Path(f"{args.out}.128.png")
    p64 = Path(f"{args.out}.64.png")
    board_preview(placements, p128, p64)
    print(f"wrote {p128} and {p64}")
    return 0


def block_placements(
    tiles: list[np.ndarray], cols: int
) -> list[tuple[int, int, np.ndarray, bool]]:
    """Tiles laid out as a compact block of neighbours (flat-top, odd-q columns)."""
    out = []
    for i, tile in enumerate(tiles):
        row, col = divmod(i, cols)
        q = col
        r = row - (col - (col & 1)) // 2
        out.append((q, r, tile, False))
    return out


def ring_step(rgba: np.ndarray) -> float:
    """Mean luminance of the pinned rim (d < 1.5) minus a ground ring at d 4-9.

    A non-zero step reads on the board as a light or dark hex outline. Feature
    pixels (strongly darker/colourful) are excluded with a saturation/value
    window around the rim's own colour.
    """
    d = tile_distance()
    rgb = rgba[..., :3].astype(np.float64)
    lum = luminance(rgb)
    rim = (d >= 0) & (d < 1.5)
    ring = (d >= 4) & (d < 9)
    ref = float(np.median(lum[rim]))
    ground = ring & (np.abs(lum - ref) < 25)
    return float(lum[rim].mean() - lum[ground].mean())


def run_review(args: argparse.Namespace) -> int:
    """`review` subcommand: rim-pin-only vs ground-restore, side by side.

    Writes <name>.compare.png (base | cut | rim-only | restore | matte |
    difference) and a block-of-neighbours preview of each mode at 128 and 64 px.
    """
    sidecar = load_sidecar(args.sidecar)
    cuts = load_cuts(sidecar, args.work_dir / "cut")
    bases = bases_for(sidecar)
    profile = resolve_profile(sidecar, cuts, bases, args.work_dir)
    cfg = pin_config(sidecar, None)
    modes = {"rim": replace(cfg, restore=False), "restore": replace(cfg, restore=True)}
    alpha = output_alpha()
    rows = []
    by_mode: dict[str, list[np.ndarray]] = {m: [] for m in modes}
    for cell in sidecar["cells"]:
        name = cell["name"]
        guide = cell_guide(cell)
        base = bases[name].tile
        results = {
            m: pin_tile(cuts[name], base, alpha, guide, profile, c)
            for m, c in modes.items()
        }
        for m, res in results.items():
            by_mode[m].append(res.rgba)
        delta = np.abs(
            results["restore"].rgba[..., :3].astype(np.float64)
            - results["rim"].rgba[..., :3].astype(np.float64)
        ).mean(axis=-1)
        rows.append(
            (
                name,
                [
                    ("base", as_rgba(base, alpha)),
                    ("cut (Codex)", as_rgba(cuts[name], alpha)),
                    ("rim pin only", results["rim"].rgba),
                    ("ground restore", results["restore"].rgba),
                    ("restore matte", matte_rgba(results["restore"].matte, alpha)),
                    ("|restore-rim| x4", matte_rgba(delta * 4.0 / 255.0, alpha)),
                ],
            )
        )
    stem = sidecar["name"]
    for mode, tiles in by_mode.items():
        steps = [ring_step(t) for t in tiles]
        print(
            f"{mode}: rim-vs-inner ground luminance step median {np.median(steps):+.2f}, "
            f"max |step| {np.max(np.abs(steps)):.2f}"
        )
    out = montage(
        rows,
        args.scale,
        args.review_dir / f"{stem}.compare.png",
        f"{stem}: rim pin only vs ground restore",
    )
    print(f"wrote {out}")
    for mode, tiles in by_mode.items():
        p128 = args.review_dir / f"{stem}.block-{mode}.128.png"
        p64 = args.review_dir / f"{stem}.block-{mode}.64.png"
        board_preview(block_placements(tiles, args.cols), p128, p64)
        print(f"wrote {p128} and {p64}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI definition."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sheet", help="build the Codex input sheet + sidecar from a job")
    s.add_argument("--job", required=True, type=Path)
    s.add_argument("--out-dir", required=True, type=Path)

    c = sub.add_parser("cut", help="register a Codex output and cut 128 px cells")
    c.add_argument("--sidecar", required=True, type=Path)
    c.add_argument("--raw", required=True, type=Path)
    c.add_argument("--out-dir", required=True, type=Path)

    p = sub.add_parser("profile", help="extract a crossing profile from a cut cell")
    p.add_argument("--sidecar", required=True, type=Path)
    p.add_argument("--cut-dir", required=True, type=Path)
    p.add_argument("--cell", required=True)
    p.add_argument("--edges", type=int, nargs="+")
    p.add_argument("--bank", type=float, default=3.0)
    p.add_argument("--feather", type=float, default=4.0)
    p.add_argument("--out", required=True, type=Path)

    for name in ("pin", "gate"):
        g = sub.add_parser(name, help=f"{name} cut cells (writes tiles + report.json)")
        g.add_argument("--sidecar", required=True, type=Path)
        g.add_argument(
            "--work-dir",
            required=True,
            type=Path,
            help="dir with cut/ + registration.json",
        )
        g.add_argument("--tiles-dir", type=Path, help="default <work-dir>/tiles")
        g.add_argument("--restore", action=argparse.BooleanOptionalAction, default=None)

    r = sub.add_parser("run", help="sheet -> Codex -> cut -> pin -> gate -> review")
    r.add_argument("--job", required=True, type=Path)
    r.add_argument("--prompt", required=True, type=Path)
    r.add_argument(
        "--ref", type=Path, action="append", default=[], help="style reference image(s)"
    )
    r.add_argument("--out-dir", required=True, type=Path)
    r.add_argument("--review-dir", type=Path)
    r.add_argument("--manifest", type=Path, help="default <out-dir>/manifest.json")
    r.add_argument(
        "--from-raw", type=Path, help="replay from a saved raw; no Codex call"
    )
    r.add_argument("--overwrite-raw", action="store_true")
    r.add_argument("--restore", action=argparse.BooleanOptionalAction, default=None)

    v = sub.add_parser(
        "preview", help="adjacency preview of oriented tiles along a course"
    )
    v.add_argument("--tiles-dir", required=True, type=Path)
    v.add_argument(
        "--course",
        required=True,
        help='hexes with their connected edges, "q,r:edges;..." e.g. "0,0:5;0,1:25"',
    )
    v.add_argument(
        "--fill",
        type=Path,
        nargs="+",
        required=True,
        help="base tiles (any size) for the ring around",
    )
    v.add_argument(
        "--out", required=True, type=Path, help="writes <out>.128.png + <out>.64.png"
    )

    w = sub.add_parser("review", help="rim-pin-only vs ground-restore comparison")
    w.add_argument("--sidecar", required=True, type=Path)
    w.add_argument("--work-dir", required=True, type=Path, help="dir with cut/")
    w.add_argument("--review-dir", required=True, type=Path)
    w.add_argument("--scale", type=int, default=2)
    w.add_argument("--cols", type=int, default=3, help="block preview columns")

    a = sub.add_parser("adopt", help="wrap a hand-built probe sheet as a sidecar")
    a.add_argument("--legacy-json", required=True, type=Path)
    a.add_argument("--sheet", required=True, type=Path)
    a.add_argument(
        "--ground",
        required=True,
        type=Path,
        help="the ground image the sheet was cut from",
    )
    a.add_argument(
        "--names", required=True, help="comma-separated cell names, row-major"
    )
    a.add_argument("--prefix", required=True)
    a.add_argument("--anchors", help='JSON, e.g. {"lum": 96.7, "hue": 62}')
    a.add_argument("--out-dir", required=True, type=Path)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    handlers = {
        "sheet": run_sheet,
        "cut": run_cut,
        "profile": run_profile,
        "pin": run_pin_gate,
        "gate": run_pin_gate,
        "run": run_run,
        "preview": run_preview,
        "review": run_review,
        "adopt": run_adopt,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
