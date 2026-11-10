#!/usr/bin/env python3
"""Review images for edits of a shipped unit sprite (armor-edit-1: riders added).

Writes, under review/:
  <dir>-side.png     per edit at 128 px: deployed (blue) | edit (blue) | edit base | edit mask
  <dir>-board.png    board scale, as WorldRenderer draws it: deployed | edit on plains | on road
  <dir>-overlay.png  how closely the edited tank matches the deployed one, per edit:
                     as built (both centred by unit.py) and hull-aligned (shifted so the
                     track runs coincide), each as a red/cyan silhouette overlay (red =
                     deployed only, cyan = edit only, grey = both) plus a 0-255 |diff|
                     heat map of the base layers where both are opaque; 3x nearest.
  <dir>-tint.png     the four owner-colour tints (via review_renders.render_tint)
  <dir>-metrics.json track offset as built, silhouette IoU as built / aligned, and the
                     same IoU over the rows below the turret (hull + tracks only).

Usage: review_edit.py <edit-dir> --unit armor
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from review_renders import (
    BG,
    BOARD_ZOOM,
    DEPLOYED,
    FONT_S,
    NEUTRAL,
    OUT,
    TILE_PX,
    TINTS,
    board_tiles,
    board_unit,
    candidates,
    centred,
    label,
    mask_of,
    on,
    render_tint,
    tinted,
)

ZOOM = 3
CELL = 128
SOLID = 128  # alpha >= 0.5


def alpha_of(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))[..., 3] >= SOLID


def track_anchor(a: np.ndarray) -> tuple[int, int]:
    """(left x of the track run, bottom y) - the track band is the bottom 12 % of the sprite."""
    ys, _ = np.nonzero(a)
    y1 = int(ys.max())
    h = y1 - int(ys.min()) + 1
    band = a[y1 - int(0.12 * h) : y1 + 1]
    xs = np.nonzero(band.any(axis=0))[0]
    return int(xs.min()), y1


def shift(arr: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(arr)
    h, w = arr.shape[:2]
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = arr[ys, xs]
    return out


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max(1, (a | b).sum()))


def overlay_panel(dep: np.ndarray, edit: np.ndarray) -> Image.Image:
    img = np.zeros((CELL, CELL, 4), dtype=np.uint8)
    img[..., 3] = 255
    img[..., :3] = NEUTRAL[:3]
    img[dep & ~edit] = (235, 70, 60, 255)
    img[edit & ~dep] = (60, 220, 235, 255)
    img[dep & edit] = (170, 170, 170, 255)
    return Image.fromarray(img, "RGBA")


def diff_panel(dep_rgba: np.ndarray, edit_rgba: np.ndarray) -> tuple[Image.Image, float]:
    both = (dep_rgba[..., 3] >= SOLID) & (edit_rgba[..., 3] >= SOLID)
    d = np.abs(dep_rgba[..., :3].astype(int) - edit_rgba[..., :3].astype(int)).mean(axis=2)
    heat = np.zeros((CELL, CELL, 4), dtype=np.uint8)
    heat[..., 3] = 255
    heat[..., :3] = 30
    v = np.clip(d * 2, 0, 255).astype(np.uint8)  # x2 so mid differences show
    heat[both, 0] = v[both]
    heat[both, 1] = v[both]
    heat[both, 2] = v[both]
    return Image.fromarray(heat, "RGBA"), float(d[both].mean())


def render_side(edit_dir: Path, unit: str, names: list[str]) -> Path:
    pad, lab = 10, 18
    cols = ["deployed (blue)", "column edit (blue)", "edit base", "edit mask"]
    sheet = Image.new("RGBA", (pad + 4 * (CELL + pad), pad + lab + len(names) * (lab + CELL + pad)), BG)
    neutral = Image.new("RGBA", (CELL, CELL), NEUTRAL)
    blue = TINTS["viewer blue 3d7eff"]
    for k, c in enumerate(cols):
        label(sheet, (pad + k * (CELL + pad), pad), c, FONT_S)
    for r, name in enumerate(names):
        y = pad + lab + r * (lab + CELL + pad)
        label(sheet, (pad, y), f"{edit_dir.name}/{name}")
        base = edit_dir / f"{name}_base.png"
        panels = [
            tinted(DEPLOYED / f"{unit}_base.png", blue),
            tinted(base, blue),
            Image.open(base).convert("RGBA"),
            Image.open(mask_of(base)).convert("RGBA"),
        ]
        for k, p in enumerate(panels):
            sheet.paste(on(neutral, p), (pad + k * (CELL + pad), y + lab))
    path = OUT / f"{edit_dir.name}-side.png"
    sheet.convert("RGB").save(path)
    return path


def render_board_edit(edit_dir: Path, unit: str, names: list[str]) -> Path:
    plains, road = board_tiles()
    deployed = centred(plains, [(board_unit(DEPLOYED / f"{unit}_base.png"), 0, 0)])
    pad, lab = 4, 9
    w = pad + 3 * (TILE_PX + pad)
    h = pad + len(names) * (lab + TILE_PX + pad)
    sheet = Image.new("RGBA", (w, h), BG)
    for r, name in enumerate(names):
        y = pad + r * (lab + TILE_PX + pad)
        u = board_unit(edit_dir / f"{name}_base.png")
        for k, p in enumerate((deployed, centred(plains, [(u, 0, 0)]), centred(road, [(u, 0, 0)]))):
            sheet.alpha_composite(p, (pad + k * (TILE_PX + pad), y + lab))
    big = sheet.resize((w * BOARD_ZOOM, h * BOARD_ZOOM), Image.NEAREST)
    for r, name in enumerate(names):
        label(big, (pad * BOARD_ZOOM, (pad + r * (lab + TILE_PX + pad)) * BOARD_ZOOM), f"{name}: deployed | column on plains | on road", FONT_S)
    path = OUT / f"{edit_dir.name}-board.png"
    big.convert("RGB").save(path)
    return path


def render_overlay(edit_dir: Path, unit: str, names: list[str]) -> tuple[Path, dict]:
    dep_rgba = np.asarray(Image.open(DEPLOYED / f"{unit}_base.png").convert("RGBA"))
    dep = dep_rgba[..., 3] >= SOLID
    dep_anchor = track_anchor(dep)
    ys = np.nonzero(dep.any(axis=1))[0]
    # rows below the turret ring: the lower 55 % of the deployed sprite (hull + tracks)
    hull_from = int(ys.max() - 0.55 * (ys.max() - ys.min()))
    metrics: dict[str, dict] = {}
    pad, lab = 6, 0
    titles = ["as built", "as-built |diff|", "hull-aligned", "aligned |diff|"]
    sheet = Image.new("RGBA", (pad + 4 * (CELL + pad), pad + len(names) * (CELL + pad)), BG)
    for r, name in enumerate(names):
        e_rgba = np.asarray(Image.open(edit_dir / f"{name}_base.png").convert("RGBA"))
        e = e_rgba[..., 3] >= SOLID
        ax, ay = track_anchor(e)
        dx, dy = dep_anchor[0] - ax, dep_anchor[1] - ay
        e_al = shift(e, dx, dy)
        e_rgba_al = shift(e_rgba, dx, dy)
        diff_built, mad_built = diff_panel(dep_rgba, e_rgba)
        diff_al, mad_al = diff_panel(dep_rgba, e_rgba_al)
        lower = np.zeros_like(dep)
        lower[hull_from:] = True
        metrics[name] = {
            "track_offset_as_built_px": [-dx, -dy],
            "iou_as_built": round(iou(dep, e), 3),
            "iou_aligned": round(iou(dep, e_al), 3),
            "iou_aligned_hull_rows": round(iou(dep & lower, e_al & lower), 3),
            "mean_abs_diff_aligned_0_255": round(mad_al, 1),
            "mean_abs_diff_as_built_0_255": round(mad_built, 1),
        }
        y = pad + r * (CELL + pad)
        for k, p in enumerate((overlay_panel(dep, e), diff_built, overlay_panel(dep, e_al), diff_al)):
            sheet.paste(p, (pad + k * (CELL + pad), y))
    big = sheet.resize((sheet.width * ZOOM, sheet.height * ZOOM), Image.NEAREST)
    for r, name in enumerate(names):
        y = (pad + r * (CELL + pad)) * ZOOM + 2
        for k, t in enumerate(titles):
            m = metrics[name]
            extra = {
                0: f"  IoU {m['iou_as_built']}  offset {m['track_offset_as_built_px']}",
                2: f"  IoU {m['iou_aligned']} (hull rows {m['iou_aligned_hull_rows']})",
                3: f"  mean {m['mean_abs_diff_aligned_0_255']}",
            }.get(k, f"  mean {m['mean_abs_diff_as_built_0_255']}")
            label(big, ((pad + k * (CELL + pad)) * ZOOM + 4, y), f"{name} {t}{extra}", FONT_S)
    path = OUT / f"{edit_dir.name}-overlay.png"
    big.convert("RGB").save(path)
    return path, metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edit_dir", type=Path)
    ap.add_argument("--unit", required=True)
    args = ap.parse_args()
    d = args.edit_dir.resolve()
    names = [n for n, r in candidates(d) if "gate_failure" not in r]
    print(render_side(d, args.unit, names))
    print(render_board_edit(d, args.unit, names))
    path, metrics = render_overlay(d, args.unit, names)
    print(path)
    print(render_tint(d))
    mpath = OUT / f"{d.name}-metrics.json"
    mpath.write_text(json.dumps(metrics, indent=1) + "\n")
    print(json.dumps(metrics, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
