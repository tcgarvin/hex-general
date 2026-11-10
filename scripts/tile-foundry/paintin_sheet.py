"""Job files, sheet layout, and sheet registration + cutting for `paintin.py`.

A **job** (JSON, validated here — the boundary) names the cells of one Codex
paint-in sheet: each cell's base tile, optional oriented guide, and output
name, plus the sheet layout and the pin/profile/anchor settings. `build_sheet`
renders the sheet (separated flat-top hexagons on flat #FF00FF) and a sidecar
JSON that is the single source of truth for every later stage. `register` fits
Codex's output back onto the sidecar layout from the magenta/hexagon
silhouettes (isotropic scale + translation), and `cut_cells` resamples each
registered cell to a 128 px tile.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from cut import bleed_mask
from hexgeom import hex_mask
from paintin_geom import (
    DEFAULT_GUIDE_COLOR,
    GUIDE_KINDS,
    HEX_WIDTH,
    TILE_PX,
    Guide,
    Marker,
    hex_coverage,
    hex_inset_distance,
    marker_extras_from_sidecar,
    parse_guide_style,
    parse_marker_extras,
    pixel_grid,
    render_guide_mask,
    render_marker_mask,
    style_from_sidecar,
    tile_distance,
)

MAGENTA = (255, 0, 255)
REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_VERSION = 1
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
MIN_GAP_PX = 24  # minimum magenta gap between neighbouring hexagons on a sheet

# hex_width / 128 is the sheet-to-tile reduction: 3x3 paints at 2.66:1, 4x4 at
# 2:1, 5x5 at 1.5:1, 6x6 at 1:1 (the concept's own scale, so Codex's 1-3 px
# grain survives the cut unresampled).
LAYOUT_PRESETS: dict[str, dict[str, int]] = {
    "3x3": {"canvas": 1254, "cols": 3, "rows": 3, "hex_width": 340},
    "4x4": {"canvas": 1254, "cols": 4, "rows": 4, "hex_width": 256},
    "5x5": {"canvas": 1254, "cols": 5, "rows": 5, "hex_width": 192},
    "6x6": {"canvas": 1254, "cols": 6, "rows": 6, "hex_width": 128},
}


class JobError(ValueError):
    """A job file (or a file it references) is invalid."""


class RegistrationError(RuntimeError):
    """Codex's output could not be registered to the sheet layout."""


# ---------------------------------------------------------------- the job ----


@dataclass(frozen=True)
class Layout:
    """Sheet geometry: a `cols` x `rows` grid of hexagons on a square canvas."""

    canvas: int
    cols: int
    rows: int
    hex_width: int

    def slot_centre(self, slot: int) -> tuple[float, float]:
        """Centre of grid slot `slot` (row-major), with an integer-aligned hex box."""
        row, col = divmod(slot, self.cols)
        pitch = self.canvas / self.cols, self.canvas / self.rows
        x0 = round((col + 0.5) * pitch[0] - self.hex_width / 2.0)
        y0 = round((row + 0.5) * pitch[1] - self.hex_width / 2.0)
        return (x0 + self.hex_width / 2.0, y0 + self.hex_width / 2.0)

    @property
    def slots(self) -> int:
        """Number of hexagon slots on the sheet."""
        return self.cols * self.rows


@dataclass(frozen=True)
class Cell:
    """One hexagon of a sheet."""

    name: str
    slot: int
    base: Path
    guide: Guide | None  # None: a non-oriented cell (no guide drawn)
    anchors: dict[str, float] = field(default_factory=dict)  # overrides job anchors


@dataclass(frozen=True)
class Job:
    """A validated paint-in job."""

    path: Path
    name: str
    layout: Layout
    cells: tuple[Cell, ...]
    profile: dict[str, Any]
    pin: dict[str, Any]
    gates: dict[str, Any]
    anchors: dict[str, float]


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise JobError(msg)


def _parse_layout(raw: Any, where: str) -> Layout:
    if isinstance(raw, str):
        _require(raw in LAYOUT_PRESETS, f"{where}: unknown layout preset {raw!r}")
        raw = LAYOUT_PRESETS[raw]
    _require(isinstance(raw, dict), f"{where}: layout must be a preset name or object")
    keys = ("canvas", "cols", "rows", "hex_width")
    for k in keys:
        _require(
            isinstance(raw.get(k), int) and raw[k] > 0,
            f"{where}: layout.{k} must be a positive integer, got {raw.get(k)!r}",
        )
    layout = Layout(**{k: raw[k] for k in keys})
    pitch_x = layout.canvas / layout.cols
    pitch_y = layout.canvas / layout.rows
    gap_x = pitch_x - layout.hex_width
    gap_y = pitch_y - layout.hex_width * math.sqrt(3) / 2.0
    _require(
        min(gap_x, gap_y) >= MIN_GAP_PX,
        f"{where}: hexagons too close (gaps {gap_x:.0f}x{gap_y:.0f} px, need {MIN_GAP_PX})",
    )
    _require(layout.hex_width >= TILE_PX, f"{where}: hex_width must be >= {TILE_PX}")
    return layout


def _parse_color(raw: Any, where: str) -> tuple[int, int, int]:
    _require(
        isinstance(raw, list)
        and len(raw) == 3
        and all(isinstance(v, int) and 0 <= v <= 255 for v in raw),
        f"{where}: colour must be [r, g, b] with ints 0..255, got {raw!r}",
    )
    return (raw[0], raw[1], raw[2])


def _or_job_error(parse: Any, raw: dict[str, Any], where: str) -> dict[str, Any]:
    """Run a paintin_geom field parser, re-raising its ValueError as a JobError."""
    try:
        return parse(raw)
    except ValueError as err:
        raise JobError(f"{where}: {err}") from err


def _parse_guide(raw: Any, where: str) -> Guide:
    _require(isinstance(raw, dict), f"{where}: guide must be an object")
    kind = raw.get("kind")
    _require(kind in GUIDE_KINDS, f"{where}: guide.kind must be one of {GUIDE_KINDS}")
    edges = raw.get("edges")
    _require(
        isinstance(edges, list) and all(isinstance(e, int) for e in edges),
        f"{where}: guide.edges must be a list of edge indices",
    )
    width = raw.get("width")
    _require(
        isinstance(width, (int, float)) and 4 <= width <= 48,
        f"{where}: guide.width must be 4..48 tile px, got {width!r}",
    )
    color = (
        _parse_color(raw["color"], f"{where}.color")
        if "color" in raw
        else DEFAULT_GUIDE_COLOR[kind]
    )
    marker = None
    if "marker" in raw:
        m = raw["marker"]
        _require(isinstance(m, dict), f"{where}: marker must be an object")
        axis = m.get("axis_edges")
        _require(
            isinstance(axis, list) and len(axis) == 2,
            f"{where}: marker.axis_edges must be two opposite edges",
        )
        marker = Marker(
            kind=str(m.get("kind", "bridge")),
            axis_edges=(int(axis[0]), int(axis[1])),
            length=float(m["length"]),
            width=float(m["width"]),
            color=_parse_color(m["color"], f"{where}.marker.color"),
            **_or_job_error(parse_marker_extras, m, where),
        )
    guide = Guide(
        kind=kind,
        edges=tuple(sorted(edges)),
        width=float(width),
        color=color,
        marker=marker,
        **_or_job_error(parse_guide_style, raw, where),
    )
    try:
        guide.courses()
    except ValueError as err:
        raise JobError(f"{where}: {err}") from err
    return guide


def load_job(path: Path) -> Job:
    """Load and validate a job file. Paths in it resolve against the job's directory."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as err:
        raise JobError(f"{path}: cannot read job: {err}") from err
    _require(isinstance(raw, dict), f"{path}: job must be a JSON object")
    name = raw.get("name")
    _require(
        isinstance(name, str) and bool(NAME_RE.match(name)),
        f"{path}: name must match {NAME_RE.pattern}",
    )
    layout = _parse_layout(raw.get("layout"), f"{path}")
    cells_raw = raw.get("cells")
    _require(
        isinstance(cells_raw, list) and len(cells_raw) > 0,
        f"{path}: cells must be a non-empty list",
    )
    _require(
        len(cells_raw) <= layout.slots,
        f"{path}: {len(cells_raw)} cells but the layout has {layout.slots} slots",
    )
    cells: list[Cell] = []
    seen_names: set[str] = set()
    seen_slots: set[int] = set()
    for i, c in enumerate(cells_raw):
        where = f"{path}: cells[{i}]"
        _require(isinstance(c, dict), f"{where} must be an object")
        cname = c.get("name")
        _require(
            isinstance(cname, str) and bool(NAME_RE.match(cname)),
            f"{where}: name must match {NAME_RE.pattern}",
        )
        _require(cname not in seen_names, f"{where}: duplicate name {cname!r}")
        seen_names.add(cname)
        slot = c.get("slot", i)
        _require(
            isinstance(slot, int) and 0 <= slot < layout.slots,
            f"{where}: slot must be 0..{layout.slots - 1}, got {slot!r}",
        )
        _require(slot not in seen_slots, f"{where}: slot {slot} used twice")
        seen_slots.add(slot)
        base_rel = c.get("base")
        _require(isinstance(base_rel, str), f"{where}: base must be a path string")
        base = (path.parent / base_rel).resolve()
        _require(base.is_file(), f"{where}: base tile {base} does not exist")
        guide = _parse_guide(c["guide"], f"{where}.guide") if "guide" in c else None
        anchors = c.get("anchors", {})
        _require(
            isinstance(anchors, dict)
            and all(isinstance(v, (int, float)) for v in anchors.values()),
            f"{where}: anchors must map names to numbers",
        )
        cells.append(
            Cell(
                name=cname,
                slot=slot,
                base=base,
                guide=guide,
                anchors={k: float(v) for k, v in anchors.items()},
            )
        )
    profile = raw.get("profile", {})
    _require(isinstance(profile, dict), f"{path}: profile must be an object")
    if any(c.guide is not None for c in cells):
        _require(
            "path" in profile or "extract" in profile,
            f"{path}: oriented cells need profile.path or profile.extract",
        )
    if "path" in profile:
        p = (path.parent / profile["path"]).resolve()
        _require(p.is_file(), f"{path}: profile.path {p} does not exist")
        profile = {**profile, "path": str(p)}
    if "extract" in profile:
        src = profile["extract"].get("cell")
        _require(
            any(c.name == src and c.guide is not None for c in cells),
            f"{path}: profile.extract.cell {src!r} is not an oriented cell of this job",
        )
    for key in ("pin", "gates", "anchors"):
        _require(isinstance(raw.get(key, {}), dict), f"{path}: {key} must be an object")
    return Job(
        path=path.resolve(),
        name=name,
        layout=layout,
        cells=tuple(cells),
        profile=profile,
        pin=raw.get("pin", {}),
        gates=raw.get("gates", {}),
        anchors={k: float(v) for k, v in raw.get("anchors", {}).items()},
    )


# ------------------------------------------------------------- base tiles ----


@dataclass(frozen=True)
class BaseTile:
    """A base tile: the master (any square size >= 128) and its 128 px version."""

    path: Path
    master: np.ndarray  # float64 (N, N, 3)
    tile: np.ndarray  # float64 (128, 128, 3)


def output_alpha() -> np.ndarray:
    """The asset-spec alpha: hex mask dilated 1 px (uint8, 128 x 128)."""
    return bleed_mask(hex_mask(TILE_PX, HEX_WIDTH), 1)


def load_base(path: Path) -> BaseTile:
    """Load a base tile and validate it covers the hex (plus the bleed ring)."""
    try:
        img = Image.open(path)
        img.load()
    except OSError as err:
        raise JobError(f"{path}: cannot read base tile: {err}") from err
    if img.width != img.height or img.width < TILE_PX:
        raise JobError(
            f"{path}: base must be square and >= {TILE_PX} px, got {img.size}"
        )
    if img.mode == "RGBA":
        alpha = np.asarray(img.getchannel("A"))
        size = img.width
        need = tile_distance(size, float(size)) >= -1.0 * size / TILE_PX
        if (alpha[need] < 250).any():
            raise JobError(
                f"{path}: base alpha is not opaque over the hex + 1 px bleed ring "
                f"({int((alpha[need] < 250).sum())} px transparent)"
            )
    rgb = img.convert("RGB")
    master = np.asarray(rgb, dtype=np.float64)
    if img.width == TILE_PX:
        tile = master
    else:
        tile = np.asarray(
            rgb.resize((TILE_PX, TILE_PX), Image.Resampling.LANCZOS), dtype=np.float64
        )
    return BaseTile(path=path, master=master, tile=tile)


def sha256_of(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_rel(path: Path) -> str:
    """`path` relative to the repo root when it lies inside it, else absolute."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def repo_path(stored: str) -> Path:
    """Inverse of `repo_rel`."""
    p = Path(stored)
    return p if p.is_absolute() else REPO_ROOT / p


# ---------------------------------------------------------------- sheet ----


def guide_to_json(guide: Guide) -> dict[str, Any]:
    """Serialise a guide for the sidecar."""
    return asdict(guide)


def guide_from_json(raw: dict[str, Any]) -> Guide:
    """Inverse of `guide_to_json` (sidecar content is trusted)."""
    marker = None
    if raw["marker"] is not None:
        m = raw["marker"]
        marker = Marker(
            kind=m["kind"],
            axis_edges=tuple(m["axis_edges"]),  # type: ignore[arg-type]
            length=m["length"],
            width=m["width"],
            color=tuple(m["color"]),  # type: ignore[arg-type]
            **marker_extras_from_sidecar(m),
        )
    return Guide(
        kind=raw["kind"],
        edges=tuple(raw["edges"]),
        width=raw["width"],
        color=tuple(raw["color"]),  # type: ignore[arg-type]
        marker=marker,
        **style_from_sidecar(raw),
    )


def render_cell(base: BaseTile, guide: Guide | None, hex_width: int) -> np.ndarray:
    """A cell's sheet content (hex_width square, float RGB): base + guide, unmasked."""
    resized = Image.fromarray(np.round(base.master).astype(np.uint8)).resize(
        (hex_width, hex_width), Image.Resampling.LANCZOS
    )
    rgb = np.asarray(resized, dtype=np.float64)
    if guide is not None:
        band = render_guide_mask(guide, hex_width, float(hex_width))[..., None]
        rgb = rgb * (1 - band) + np.asarray(guide.color, dtype=np.float64) * band
        if guide.marker is not None:
            mk = render_marker_mask(guide.marker, hex_width, float(hex_width))[
                ..., None
            ]
            rgb = rgb * (1 - mk) + np.asarray(guide.marker.color, dtype=np.float64) * mk
    return rgb


def build_sheet(job: Job, out_dir: Path) -> tuple[Path, Path]:
    """Render `job`'s sheet and sidecar into `out_dir`; returns (sheet, sidecar) paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    lay = job.layout
    sheet = np.empty((lay.canvas, lay.canvas, 3), dtype=np.float64)
    sheet[:] = MAGENTA
    cells_json = []
    w = lay.hex_width
    cover = hex_coverage(w, w, (w / 2.0, w / 2.0), float(w))[..., None]
    for cell in job.cells:
        base = load_base(cell.base)
        cx, cy = lay.slot_centre(cell.slot)
        x0, y0 = int(cx - w / 2.0), int(cy - w / 2.0)
        content = render_cell(base, cell.guide, w)
        region = sheet[y0 : y0 + w, x0 : x0 + w]
        sheet[y0 : y0 + w, x0 : x0 + w] = content * cover + region * (1 - cover)
        cells_json.append(
            {
                "name": cell.name,
                "slot": cell.slot,
                "cx": cx,
                "cy": cy,
                "hex_width": w,
                "scale": w / HEX_WIDTH,
                "base": repo_rel(cell.base),
                "base_sha256": sha256_of(cell.base),
                "guide": None if cell.guide is None else guide_to_json(cell.guide),
                "anchors": cell.anchors,
                "output": f"{cell.name}.png",
            }
        )
    sheet_path = out_dir / f"{job.name}.sheet.png"
    Image.fromarray(np.round(sheet).astype(np.uint8)).save(sheet_path)
    sidecar = {
        "tool": "scripts/tile-foundry/paintin.py",
        "version": SIDECAR_VERSION,
        "job": repo_rel(job.path),
        "job_sha256": sha256_of(job.path),
        "name": job.name,
        "sheet": repo_rel(sheet_path),
        "sheet_sha256": sha256_of(sheet_path),
        "canvas": [lay.canvas, lay.canvas],
        "layout": asdict(lay),
        "background": list(MAGENTA),
        "tile_px": TILE_PX,
        "profile": job.profile,
        "pin": job.pin,
        "gates": job.gates,
        "anchors": job.anchors,
        "cells": cells_json,
    }
    sidecar_path = out_dir / f"{job.name}.sheet.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=1))
    return sheet_path, sidecar_path


def load_sidecar(path: Path) -> dict[str, Any]:
    """Load a sheet sidecar written by `build_sheet` (or `adopt`)."""
    try:
        sidecar = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as err:
        raise JobError(f"{path}: cannot read sidecar: {err}") from err
    if sidecar.get("version") != SIDECAR_VERSION:
        raise JobError(
            f"{path}: sidecar version {sidecar.get('version')!r} unsupported"
        )
    return sidecar


def cell_guide(cell: dict[str, Any]) -> Guide | None:
    """The guide of a sidecar cell, or None for a non-oriented cell."""
    return None if cell["guide"] is None else guide_from_json(cell["guide"])


# --------------------------------------------------------- registration ----

BG_NOISE_MARGIN = 30.0  # magenta-ness this far below the background level is foreground
FG_FLOOR = 40.0  # magenta-ness at or below this is fully foreground


def magenta_level(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel magenta-ness `min(r, b) - g` (about 249 on Codex's magenta)."""
    return np.minimum(rgb[..., 0], rgb[..., 2]) - rgb[..., 1]


def foreground_weight(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    """Soft foreground weight (0 on the magenta background, 1 on painted pixels).

    Returns the weight map and the measured background level. The ramp's top
    sits `BG_NOISE_MARGIN` below the background level so background noise maps
    to exactly 0 and cannot bias centroids.
    """
    level = magenta_level(rgb)
    bg = level[level > 150]
    if bg.size < 0.05 * level.size:
        raise RegistrationError(
            f"no magenta background found ({bg.size} px with magenta-ness > 150)"
        )
    bg_level = float(np.median(bg))
    return foreground_weight_at(level, bg_level), bg_level


def foreground_weight_at(level: np.ndarray, bg_level: float) -> np.ndarray:
    """Foreground weight from magenta-ness `level` and a known background level."""
    hi = bg_level - BG_NOISE_MARGIN
    return 1.0 - np.clip((level - FG_FLOOR) / (hi - FG_FLOOR), 0.0, 1.0)


@dataclass
class CellFit:
    """Registration measurements for one cell (output-image px unless noted)."""

    name: str
    expected: tuple[float, float]
    measured: tuple[float, float]
    width_ratio: float
    residual_px: float = 0.0
    residual_tile_px: float = 0.0
    iou: float = 0.0


@dataclass
class Registration:
    """sheet -> output mapping `out = scale * sheet + (tx, ty)` plus per-cell fits."""

    scale: float
    tx: float
    ty: float
    output_size: tuple[int, int]
    background_level: float
    cells: dict[str, CellFit] = field(default_factory=dict)

    def to_output(self, x: float, y: float) -> tuple[float, float]:
        """Map a sheet coordinate into the output image."""
        return (self.scale * x + self.tx, self.scale * y + self.ty)

    def to_json(self) -> dict[str, Any]:
        """Serialisable summary."""
        return {
            "scale": self.scale,
            "tx": self.tx,
            "ty": self.ty,
            "output_size": list(self.output_size),
            "background_level": self.background_level,
            "max_residual_tile_px": max(
                c.residual_tile_px for c in self.cells.values()
            ),
            "min_iou": min(c.iou for c in self.cells.values()),
            "cells": {k: asdict(v) for k, v in self.cells.items()},
        }


def _measure_cell(
    weight: np.ndarray, centre: tuple[float, float], hex_w: float, roi_half: float
) -> tuple[tuple[float, float], float]:
    """Weighted centroid and area-derived hex width inside a square ROI."""
    h, w = weight.shape
    x0 = max(0, int(centre[0] - roi_half))
    x1 = min(w, int(centre[0] + roi_half) + 1)
    y0 = max(0, int(centre[1] - roi_half))
    y1 = min(h, int(centre[1] + roi_half) + 1)
    roi = weight[y0:y1, x0:x1]
    area = float(roi.sum())
    expected_area = 3.0 * math.sqrt(3.0) / 8.0 * hex_w * hex_w
    if area < 0.5 * expected_area:
        raise RegistrationError(
            f"hexagon not found near ({centre[0]:.0f}, {centre[1]:.0f}): foreground "
            f"area {area:.0f} px vs expected {expected_area:.0f}"
        )
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    cx = float((roi * (xs + 0.5)).sum() / area)
    cy = float((roi * (ys + 0.5)).sum() / area)
    width = math.sqrt(area * 8.0 / (3.0 * math.sqrt(3.0)))
    return (cx, cy), width


def _fit(
    sheet_pts: np.ndarray, out_pts: np.ndarray, width_ratios: np.ndarray
) -> tuple[float, float, float]:
    """Least-squares isotropic scale + translation (scale from widths if < 3 cells)."""
    n = len(sheet_pts)
    if n >= 3:
        a = np.zeros((2 * n, 3))
        b = np.zeros(2 * n)
        a[0::2, 0] = sheet_pts[:, 0]
        a[0::2, 1] = 1.0
        a[1::2, 0] = sheet_pts[:, 1]
        a[1::2, 2] = 1.0
        b[0::2] = out_pts[:, 0]
        b[1::2] = out_pts[:, 1]
        sol, *_ = np.linalg.lstsq(a, b, rcond=None)
        return float(sol[0]), float(sol[1]), float(sol[2])
    scale = float(np.median(width_ratios))
    t = (out_pts - scale * sheet_pts).mean(axis=0)
    return scale, float(t[0]), float(t[1])


def register(
    output_rgb: np.ndarray,
    sidecar: dict[str, Any],
    max_residual_tile_px: float = 0.75,
    max_width_error: float = 0.02,
    min_iou: float = 0.97,
) -> Registration:
    """Register Codex's output to the sheet layout and check every cell.

    Fits `out = s * sheet + t` to the cells' foreground centroids (two passes:
    a global area/centroid estimate seeds per-cell ROIs, then a refit), then
    checks per cell the centre residual (in 128 px tile units), the
    area-derived width against `s * hex_width`, and the silhouette IoU against
    the predicted hexagon. Raises RegistrationError naming every failing cell.
    """
    weight, bg_level = foreground_weight(output_rgb)
    h, w = weight.shape
    canvas_w, canvas_h = sidecar["canvas"]
    cells = sidecar["cells"]
    # seed: total foreground area and centroid vs the sheet's
    sheet_area = sum(3 * math.sqrt(3) / 8 * c["hex_width"] ** 2 for c in cells)
    out_area = float(weight.sum())
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    out_c = np.array(
        [(weight * (xs + 0.5)).sum() / out_area, (weight * (ys + 0.5)).sum() / out_area]
    )
    areas = np.array([3 * math.sqrt(3) / 8 * c["hex_width"] ** 2 for c in cells])
    sheet_c = np.array(
        [
            sum(a * c["cx"] for a, c in zip(areas, cells)) / areas.sum(),
            sum(a * c["cy"] for a, c in zip(areas, cells)) / areas.sum(),
        ]
    )
    scale = math.sqrt(out_area / sheet_area)
    tx, ty = out_c - scale * sheet_c
    pitch = min(
        canvas_w / sidecar["layout"]["cols"], canvas_h / sidecar["layout"]["rows"]
    )
    sheet_pts = np.array([[c["cx"], c["cy"]] for c in cells], dtype=np.float64)
    measured: dict[str, tuple[tuple[float, float], float]] = {}
    for _ in range(2):
        for c in cells:
            guess = (scale * c["cx"] + tx, scale * c["cy"] + ty)
            measured[c["name"]] = _measure_cell(
                weight, guess, scale * c["hex_width"], scale * pitch / 2.0
            )
        out_pts = np.array([measured[c["name"]][0] for c in cells])
        ratios = np.array([measured[c["name"]][1] / c["hex_width"] for c in cells])
        scale, tx, ty = _fit(sheet_pts, out_pts, ratios)

    reg = Registration(
        scale=scale, tx=tx, ty=ty, output_size=(w, h), background_level=bg_level
    )
    failures = []
    for c in cells:
        (mx, my), mw = measured[c["name"]]
        ex, ey = reg.to_output(c["cx"], c["cy"])
        hex_out = scale * c["hex_width"]
        resid = math.hypot(mx - ex, my - ey)
        fit = CellFit(
            name=c["name"],
            expected=(ex, ey),
            measured=(mx, my),
            width_ratio=mw / hex_out,
            residual_px=resid,
            residual_tile_px=resid * TILE_PX / hex_out,
        )
        half = int(hex_out / 2 + 6)
        bx0, by0 = int(ex - half), int(ey - half)
        if bx0 < 0 or by0 < 0 or bx0 + 2 * half > w or by0 + 2 * half > h:
            failures.append(f"{c['name']}: predicted hexagon runs off the output image")
            reg.cells[c["name"]] = fit
            continue
        pred = hex_coverage(2 * half, 2 * half, (ex - bx0, ey - by0), hex_out) > 0.5
        got = weight[by0 : by0 + 2 * half, bx0 : bx0 + 2 * half] > 0.5
        fit.iou = float((pred & got).sum() / (pred | got).sum())
        reg.cells[c["name"]] = fit
        if fit.residual_tile_px > max_residual_tile_px:
            failures.append(
                f"{c['name']}: centre residual {fit.residual_tile_px:.2f} tile px "
                f"> {max_residual_tile_px}"
            )
        if abs(fit.width_ratio - 1.0) > max_width_error:
            failures.append(
                f"{c['name']}: hexagon width off by {100 * (fit.width_ratio - 1):+.1f}% "
                f"(limit {100 * max_width_error:.1f}%)"
            )
        if fit.iou < min_iou:
            failures.append(f"{c['name']}: silhouette IoU {fit.iou:.3f} < {min_iou}")
    if failures:
        raise RegistrationError(
            f"registration failed (scale {scale:.4f}, t=({tx:.1f}, {ty:.1f})): "
            + "; ".join(failures)
        )
    return reg


# ------------------------------------------------------------------ cut ----

CUT_GUARD_TILE_PX = 0.75  # pixels this close to (or outside) the hex take the base
CUT_MAGENTA_MAX = 0.05  # pixels with more magenta weight than this take the base
CUT_MARGIN_PX = 6  # extra output px around the tile box (resampling support)


def cut_cell(
    output_rgb: np.ndarray, reg: Registration, cell: dict[str, Any], base: BaseTile
) -> np.ndarray:
    """Resample one registered cell to a 128 px float RGB tile.

    Pixels outside the hex, within `CUT_GUARD_TILE_PX` of its boundary, or
    visibly magenta are replaced by the base tile (warped to output resolution)
    before resampling, so neither the magenta background nor its antialiased
    fringe can ring into the tile. The rim pin overwrites that band anyway.
    """
    ex, ey = reg.to_output(cell["cx"], cell["cy"])
    hex_out = reg.scale * cell["hex_width"]
    k = hex_out / HEX_WIDTH  # output px per tile px
    bx0 = ex - hex_out / 2.0
    by0 = ey - hex_out / 2.0
    ix0 = math.floor(bx0) - CUT_MARGIN_PX
    iy0 = math.floor(by0) - CUT_MARGIN_PX
    size = math.ceil(hex_out) + 2 * CUT_MARGIN_PX + 1
    h, w = output_rgb.shape[:2]
    if ix0 < 0 or iy0 < 0 or ix0 + size > w or iy0 + size > h:
        raise RegistrationError(f"{cell['name']}: cut window runs off the output image")
    crop = output_rgb[iy0 : iy0 + size, ix0 : ix0 + size]
    xs, ys = pixel_grid(size)
    # tile-space coordinates of the crop's pixel centres
    tx = (xs + ix0 - bx0) / k
    ty = (ys + iy0 - by0) / k
    dist = hex_inset_distance(tx, ty)
    magenta = 1.0 - foreground_weight_at(magenta_level(crop), reg.background_level)
    replace = (dist < CUT_GUARD_TILE_PX) | (magenta > CUT_MAGENTA_MAX)
    base_img = Image.fromarray(np.round(base.tile).astype(np.uint8))
    # affine: crop pixel (u, v) -> base tile pixel ((u + ix0 - bx0) / k, ...)
    warped = base_img.transform(
        (size, size),
        Image.Transform.AFFINE,
        (1.0 / k, 0.0, (ix0 - bx0) / k, 0.0, 1.0 / k, (iy0 - by0) / k),
        resample=Image.Resampling.BICUBIC,
    )
    filled = np.where(replace[..., None], np.asarray(warped, dtype=np.float64), crop)
    src = Image.fromarray(np.clip(np.round(filled), 0, 255).astype(np.uint8))
    box = (bx0 - ix0, by0 - iy0, bx0 - ix0 + hex_out, by0 - iy0 + hex_out)
    tile = src.resize((TILE_PX, TILE_PX), Image.Resampling.LANCZOS, box=box)
    return np.asarray(tile, dtype=np.float64)
