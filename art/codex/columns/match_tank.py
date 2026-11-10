#!/usr/bin/env python3
"""Find the shipped tank inside a transporter edit; write footprint targets + match metrics.

The edit is the shipped tank re-drawn on a trailer, so its scale and position
are unknown. This registers the shipped source's cobalt panels (hue window of
unit.py's classifier, sat > 0.3, solid alpha) against the edit's: for each
scale k it FFT-correlates the scaled source mask over the edit mask and keeps
the offset with the best IoU, then refines k. The tractor's cab is cobalt too,
but it is a different shape and far smaller, so the tank wins.

Reported per edit (in <dir>/match.json):
  k                 edit px per source px for the tank
  offset            top-left of the scaled source in edit crop px
  cobalt_iou        IoU of the cobalt-panel masks at the best fit
  silhouette_iou    IoU of the solid alpha over the tank's window, rows above
                    the track bottom 15 % (where the trailer deck meets the tracks)
  tank_w_in_canvas  the tank's solid width on the 128 canvas at each footprint
                    (deployed armor = 90)

Also writes <dir>/targets.json: <name>-w90 / -w120 -> target_bbox_w 90 / 120
for the whole rig, mirror copied from the shipped armor entry.

Usage: match_tank.py <dir> --unit armor
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
FOUNDRY = REPO / "art" / "foundry"
sys.path.insert(0, str(REPO / "scripts" / "tile-foundry"))
from vignette import rgb_to_hsv  # noqa: E402

FOOTPRINTS = (90, 120)
DS = 2  # registration runs at half resolution
COBALT_SAT = 0.3
TRACK_BAND = 0.15


def load(path: Path) -> tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(Image.open(path).convert("RGBA")).astype(np.float64) / 255.0
    return arr[..., :3], arr[..., 3]


def cobalt_mask(rgb: np.ndarray, alpha: np.ndarray, classify: dict) -> np.ndarray:
    h, s, _ = rgb_to_hsv(rgb)
    return (h >= classify["hue_lo"]) & (h <= classify["hue_hi"]) & (s > COBALT_SAT) & (alpha >= 0.5)


def resize_mask(m: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    img = Image.fromarray((m * 255).astype(np.uint8), "L").resize(size, Image.BILINEAR)
    return np.asarray(img) >= 128


def best_offset(src: np.ndarray, edit: np.ndarray) -> tuple[float, int, int]:
    """Max-IoU placement of src inside edit (src fully inside). Returns (iou, x, y)."""
    eh, ew = edit.shape
    sh, sw = src.shape
    if sh > eh or sw > ew:
        return 0.0, 0, 0
    fe = np.fft.rfft2(edit.astype(np.float64))
    fs = np.fft.rfft2(src.astype(np.float64), s=edit.shape)
    inter = np.fft.irfft2(fe * np.conj(fs), s=edit.shape)[: eh - sh + 1, : ew - sw + 1]
    sat = np.pad(edit.astype(np.float64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    win = sat[sh:, sw:] - sat[:-sh, sw:] - sat[sh:, :-sw] + sat[:-sh, :-sw]
    win = win[: eh - sh + 1, : ew - sw + 1]
    a = src.sum()
    iou = inter / np.maximum(a + win - inter, 1.0)
    y, x = np.unravel_index(int(np.argmax(iou)), iou.shape)
    return float(iou[y, x]), int(x), int(y)


def register(src_cob: np.ndarray, edit_cob: np.ndarray) -> tuple[float, int, int, float]:
    e = resize_mask(edit_cob, (edit_cob.shape[1] // DS, edit_cob.shape[0] // DS))
    sh, sw = src_cob.shape

    def at(k: float) -> tuple[float, int, int]:
        size = (max(1, round(sw * k / DS)), max(1, round(sh * k / DS)))
        return best_offset(resize_mask(src_cob, size), e)

    coarse = [(at(k), k) for k in np.arange(0.5, 2.5, 0.02)]
    (_, _, _), k0 = max(coarse, key=lambda t: t[0][0])
    fine = [(at(k), k) for k in np.arange(k0 - 0.02, k0 + 0.02, 0.002)]
    (iou, x, y), k = max(fine, key=lambda t: t[0][0])
    return float(k), x * DS, y * DS, iou


def silhouette_iou(src_a: np.ndarray, edit_a: np.ndarray, k: float, x: int, y: int) -> float:
    sh, sw = src_a.shape
    size = (round(sw * k), round(sh * k))
    s = resize_mask(src_a >= 0.5, size)
    win = np.zeros_like(s)
    eh, ew = edit_a.shape
    y1, x1 = min(eh, y + size[1]), min(ew, x + size[0])
    win[: y1 - y, : x1 - x] = edit_a[y:y1, x:x1] >= 0.5
    rows = int(size[1] * (1.0 - TRACK_BAND))
    s, win = s[:rows], win[:rows]
    return float((s & win).sum() / max(1, (s | win).sum()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--unit", required=True)
    args = ap.parse_args()
    units = json.loads((FOUNDRY / "manifest.json").read_text())["units"]
    spec = units["units"][args.unit]
    src_rgb, src_a = load(FOUNDRY / spec["source"])
    src_cob = cobalt_mask(src_rgb, src_a, units["classify"])
    ys, xs = np.nonzero(src_a >= 0.5)
    src_solid_w = int(xs.max() - xs.min() + 1)

    match, targets = {}, {}
    for crop in sorted((args.dir / "src").glob("*-w90.png")):
        stem = re.sub(r"-w90$", "", crop.stem)
        rgb, a = load(crop)
        k, x, y, ciou = register(src_cob, cobalt_mask(rgb, a, units["classify"]))
        eys, exs = np.nonzero(a >= 0.5)
        rig_w = int(exs.max() - exs.min() + 1)
        match[stem] = {
            "k": round(k, 3),
            "offset": [x, y],
            "cobalt_iou": round(ciou, 3),
            "silhouette_iou": round(silhouette_iou(src_a, a, k, x, y), 3),
            "rig_over_tank_width": round(rig_w / (src_solid_w * k), 2),
            "tank_w_in_canvas": {
                f"w{fp}": round(src_solid_w * k * fp / rig_w, 1) for fp in FOOTPRINTS
            },
        }
        for fp in FOOTPRINTS:
            targets[f"{stem}-w{fp}"] = {"target_bbox_w": fp, "mirror": spec["mirror"]}
        print(stem, match[stem])
    (args.dir / "match.json").write_text(json.dumps(match, indent=1) + "\n")
    (args.dir / "targets.json").write_text(json.dumps(targets, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
