#!/usr/bin/env python3
"""River (oriented straight_v) tile pipeline: guide → gen → cut → recolor.

Rivers are *oriented* terrain — the water must cross the two flat (N/S) edges
at the edge midpoints so adjacent river hexes join seamlessly — so they can't
use the generic `cut.py` window search. This script owns the river-specific
stages around the shared foundry (`gen.py` does the NBP call as usual):

  guide    Node-straightened meander layout diagram for gen.py: a grey band
           on white whose centerline runs dead straight through every
           tile-period node (where the flat-edge crossings land) and bends in
           a single sin² out-and-back lobe between nodes — so every cut tile
           enters/leaves its flat edges straight, with meanders visually
           finished before the edge.
  cut      Search a generated swatch for hex windows whose crossings satisfy
           the tiling contract (centre + width match at both flat edges,
           straight entry spans), LANCZOS-downscale WIN→128, and report seam
           ratios vs the plains picks.
  recolor  Grass-weighted HSV palette-match toward the plains picks' medians
           (the ratified hills grass_match, with the saturation ramp raised
           so the desaturated grey-green water keeps its colour).
  build    Manifest-driven replay: re-derive the applied tiles byte-for-byte
           from the committed swatch (recorded cut windows + recolor), and
           re-check the committed guide against its recorded parameters.
           `--verify` compares against the ratified tiles (selftest hook).

Typical ratification flow (all picks are the user's call, never auto-apply):

    river.py guide --win 384 --band 40 --out guide.png
    gen.py --prompt-file river-prompt.txt --ref <grass_ref> --ref <creek_ref> \
           --ref guide.png --image-size 2K --out swatch.png
    river.py cut swatch.png --out-dir cands --win 384
    river.py recolor cands cands-recolored
    # user ratifies from hosted montages, then windows go into manifest.json
    river.py build --manifest art/foundry/manifest.json --section river \
                   --out-dir /tmp/river --verify frontend/src/board/tiles/river
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# two float HSV implementations live in the foundry with different epsilon
# handling; each stage must keep the one its ratified output was built with
# (cut's for water detection, vignette's for the recolour) or byte-for-byte
# reproducibility breaks.
from cut import bleed_mask  # noqa: E402
from cut import rgb_to_hsv as cut_rgb_to_hsv  # noqa: E402
from gate import (  # noqa: E402
    SEAM_BASELINE_FLOOR,
    dc_step,
    decorrelated_baseline,
    load_tile,
    seam_ratio_texture,
)
from hexgeom import hex_mask  # noqa: E402
from vignette import hsv_to_rgb  # noqa: E402
from vignette import rgb_to_hsv as vignette_rgb_to_hsv  # noqa: E402

TILE = 128
GUIDE_SIZE = 2048
WATER_SAT_MAX = 0.30
SEAM_MAX = 2.0

# cut gates, in TILE px; scaled to swatch px by WIN/TILE at runtime
T_EDGE_CENTRE_OFF = 3.0  # crossing centre vs canonical edge midpoint
T_TOP_BOT_MISMATCH = 2.0  # top vs bottom centre disagreement
T_WIDTH_MISMATCH = 4.0  # top vs bottom width disagreement
T_WIDTH_BAND = (12.0, 36.0)  # crossing water width
T_CENTRE_DRIFT = 26.0  # meander wander inside the tile
T_WATER_HALF_SPAN = 31.0  # water stays inside the flat-edge x-band
T_EDGE_STRAIGHT_SPAN = 18.0  # rows adjacent to each flat edge that must run straight
T_EDGE_STRAIGHT_DEV = 2.5  # max centreline x-deviation within that span

# recolor: grass weight ramps. The saturation ramp starts ABOVE the water's
# saturation (~0.21, vs hills' 0.12 start which would catch it) so the water
# keeps its ratified colour; tan banks sit outside the hue window.
SAT_RAMP = (0.30, 0.42)
HUE_RAMP_LO = (30.0, 40.0)
HUE_RAMP_HI = (165.0, 175.0)
CORE_W = 0.9


# ---------------------------------------------------------------- guide ----


def build_guide(win: int, band: float, seed: int) -> Image.Image:
    """Node-straightened meander band, periodic with the cut window's flat edges.

    Period = 2*EDGE_Y at cut scale (√3/2 · win). Around each period node the
    centerline holds a straight vertical plateau; between plateaus it swings
    in one smooth sin² out-and-back lobe (zero slope at both ends), lobes
    alternating side with rng-varied amplitude.
    """
    period = float(np.sqrt(3) / 2 * win)
    plateau = 24.0 * (win / TILE) / 2  # straight half-run around each node (swatch px)

    rng = np.random.default_rng(seed)
    y = np.arange(GUIDE_SIZE, dtype=np.float64)
    centre = np.full(GUIDE_SIZE, GUIDE_SIZE / 2.0)

    n_cells = int(np.ceil(GUIDE_SIZE / period)) + 1
    for k in range(n_cells):
        y0 = k * period + plateau  # bend start
        y1 = (k + 1) * period - plateau  # bend end
        amp = (1 if k % 2 == 0 else -1) * rng.uniform(0.7, 1.0) * 38.0 * (win / 320)
        sel = (y >= y0) & (y < y1)
        t = (y[sel] - y0) / (y1 - y0)
        centre[sel] += amp * np.sin(np.pi * t) ** 2

    img = np.full((GUIDE_SIZE, GUIDE_SIZE), 255, dtype=np.uint8)
    xs = np.arange(GUIDE_SIZE, dtype=np.float64)[None, :]
    img[np.abs(xs - centre[:, None]) <= band / 2.0] = 128
    return Image.fromarray(img, mode="L").convert("RGB")


# ------------------------------------------------------------------ cut ----


def channel_runs(water_row: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous [start, end] index runs of True in a 1-D bool row."""
    xs = np.where(water_row)[0]
    if len(xs) == 0:
        return []
    runs = []
    start = prev = xs[0]
    for x in xs[1:]:
        if x != prev + 1:
            runs.append((start, prev))
            start = x
        prev = x
    runs.append((start, prev))
    return runs


def channel_profile(
    water: np.ndarray, guess_x: int, band: int = 220
) -> tuple[np.ndarray, np.ndarray]:
    """Per-row channel centre and width from the longest water run near `guess_x`.

    Rows with no water run in the band get width 0 and centre NaN — the
    candidate loop rejects any window touching such a row.
    """
    h = water.shape[0]
    centres = np.full(h, np.nan)
    widths = np.zeros(h)
    lo, hi = guess_x - band, guess_x + band
    for y in range(h):
        runs = channel_runs(water[y, lo:hi])
        if not runs:
            continue
        start, end = max(runs, key=lambda r: r[1] - r[0])
        centres[y] = lo + (start + end) / 2.0
        widths[y] = end - start + 1
    return centres, widths


class Gates:
    """The cut gates in swatch px for one window size."""

    def __init__(self, win: int):
        k = win / TILE
        self.win = win
        self.edge_y = 0.8660254 * win / 2.0  # sqrt(3)/2 * win/2: flat-edge offset
        self.max_edge_centre_off = T_EDGE_CENTRE_OFF * k
        self.max_top_bot_mismatch = T_TOP_BOT_MISMATCH * k
        self.max_width_mismatch = T_WIDTH_MISMATCH * k
        self.width_band = (T_WIDTH_BAND[0] * k, T_WIDTH_BAND[1] * k)
        self.max_centre_drift = T_CENTRE_DRIFT * k
        self.max_water_half_span = T_WATER_HALF_SPAN * k
        self.edge_straight_span = int(round(T_EDGE_STRAIGHT_SPAN * k))
        self.edge_straight_dev = T_EDGE_STRAIGHT_DEV * k

    def measure(
        self, centres: np.ndarray, widths: np.ndarray, cx: float, cy: float
    ) -> dict | None:
        """Crossing + meander metrics for a window centred at (cx, cy), or None."""
        y_top = int(round(cy - self.edge_y + 1))
        y_bot = int(round(cy + self.edge_y - 1))
        ys = np.arange(y_top, y_bot + 1)
        seg_c, seg_w = centres[ys], widths[ys]
        if np.isnan(seg_c).any() or (seg_w <= 0).any():
            return None
        span = self.edge_straight_span
        return {
            "ct": float(seg_c[0] - cx),
            "cb": float(seg_c[-1] - cx),
            "wt": float(seg_w[0]),
            "wb": float(seg_w[-1]),
            "drift": float(np.max(np.abs(seg_c - cx))),
            "water_half": float(np.max(np.abs(seg_c - cx) + seg_w / 2.0)),
            "es_top": float(np.max(np.abs(seg_c[:span] - seg_c[0]))),
            "es_bot": float(np.max(np.abs(seg_c[-span:] - seg_c[-1]))),
        }

    def passes(self, m: dict) -> bool:
        return (
            abs(m["ct"]) <= self.max_edge_centre_off
            and abs(m["cb"]) <= self.max_edge_centre_off
            and abs(m["ct"] - m["cb"]) <= self.max_top_bot_mismatch
            and abs(m["wt"] - m["wb"]) <= self.max_width_mismatch
            and self.width_band[0] <= min(m["wt"], m["wb"])
            and max(m["wt"], m["wb"]) <= self.width_band[1]
            and m["drift"] <= self.max_centre_drift
            and m["water_half"] <= self.max_water_half_span
            and m["es_top"] <= self.edge_straight_dev
            and m["es_bot"] <= self.edge_straight_dev
        )


def window_score(m: dict) -> float:
    # meander is wanted: drift isn't penalized, crossings + edge entry dominate
    return (
        abs(m["ct"])
        + abs(m["cb"])
        + 0.5 * abs(m["wt"] - m["wb"])
        + 0.5 * (m["es_top"] + m["es_bot"])
    )


def cut_window(swatch_rgb: np.ndarray, x0: int, y0: int, win: int) -> Image.Image:
    """Crop one WIN-px window, LANCZOS-downscale to TILE, apply the hex mask."""
    crop = Image.fromarray(swatch_rgb[y0 : y0 + win, x0 : x0 + win])
    small = np.asarray(crop.resize((TILE, TILE), Image.LANCZOS))
    mask = bleed_mask(hex_mask(TILE, TILE), 1)
    return Image.fromarray(np.dstack([small, mask]), mode="RGBA")


def seam_report(paths: list[Path], plains_dir: Path) -> None:
    """Print worst angled-edge seam ratio vs the plains picks + flat-self ratio."""
    plains_tiles = [load_tile(p, TILE) for p in sorted(plains_dir.glob("*.png"))]
    print(
        "\nseam ratios (texture mode, max over listed pairings; gate at "
        f"{SEAM_MAX}) + worst |dc| step:"
    )
    print(f"{'file':<28}{'angled':>8}{'flat-self':>10}{'dc':>7}")
    for p in paths:
        r_rgb = load_tile(p, TILE)[..., :3].astype(np.float64)
        r_base = max(decorrelated_baseline(r_rgb, TILE), SEAM_BASELINE_FLOOR)
        worst_angled = 0.0
        worst_dc = 0.0
        for parr in plains_tiles:
            p_rgb = parr[..., :3].astype(np.float64)
            p_base = max(decorrelated_baseline(p_rgb, TILE), SEAM_BASELINE_FLOOR)
            for d in (0, 1, 3, 4):
                worst_angled = max(
                    worst_angled,
                    seam_ratio_texture(r_rgb, p_rgb, d, TILE, r_base),
                    seam_ratio_texture(p_rgb, r_rgb, d, TILE, p_base),
                )
                worst_dc = max(worst_dc, abs(dc_step(r_rgb, p_rgb, d, TILE)))
        flat_self = max(seam_ratio_texture(r_rgb, r_rgb, d, TILE, r_base) for d in (2, 5))
        flag = "" if worst_angled <= SEAM_MAX and flat_self <= SEAM_MAX else "  <-- over gate"
        print(f"{p.name:<28}{worst_angled:>8.2f}{flat_self:>10.2f}{worst_dc:>7.1f}{flag}")


def run_cut(args: argparse.Namespace) -> int:
    gates = Gates(args.win)
    rgb = np.asarray(Image.open(args.swatch).convert("RGB"))
    h_img, w_img = rgb.shape[:2]
    _h, s, _v = cut_rgb_to_hsv(rgb.astype(np.float64) / 255.0)
    water = s < WATER_SAT_MAX

    guess_x = int(np.argmax(water.mean(axis=0)))
    centres, widths = channel_profile(water, guess_x)

    half = args.win // 2
    cands = []
    for cy in range(half, h_img - half, 2):
        y_top = int(round(cy - gates.edge_y + 1))
        y_bot = int(round(cy + gates.edge_y - 1))
        if np.isnan(centres[y_top]) or np.isnan(centres[y_bot]):
            continue
        cx = int(round((centres[y_top] + centres[y_bot]) / 2.0))
        if cx - half < 0 or cx + half > w_img:
            continue
        m = gates.measure(centres, widths, cx, cy)
        if m is None or not gates.passes(m):
            continue
        cands.append((window_score(m), cx, cy, m))

    cands.sort(key=lambda c: c[0])
    # thin out near-duplicate windows (within a hex of an already-kept one)
    kept: list[tuple[float, int, int, dict]] = []
    for c in cands:
        if all(abs(c[2] - k[2]) >= args.win for k in kept):
            kept.append(c)
        if len(kept) == args.top_n:
            break

    if not kept:
        print("no candidate window passed the crossing gates", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    k = args.win / TILE
    print(f"window {args.win}px ({k:.2f}:1); metrics in tile-scale px:")
    print(
        f"{'file':<28}{'ct':>7}{'cb':>7}{'wt':>6}{'wb':>6}{'drift':>7}{'whalf':>7}"
        f"{'esT':>6}{'esB':>6}{'score':>7}"
    )
    paths = []
    for sc, cx, cy, m in kept:
        x0, y0 = cx - half, cy - half
        p = args.out_dir / f"cand_x{x0:04d}_y{y0:04d}.png"
        cut_window(rgb, x0, y0, args.win).save(p)
        paths.append(p)
        print(
            f"{p.name:<28}{m['ct'] / k:>7.1f}{m['cb'] / k:>7.1f}{m['wt'] / k:>6.0f}"
            f"{m['wb'] / k:>6.0f}{m['drift'] / k:>7.1f}{m['water_half'] / k:>7.1f}"
            f"{m['es_top'] / k:>6.1f}{m['es_bot'] / k:>6.1f}{sc / k:>7.2f}"
        )

    seam_report(paths, args.plains)
    return 0


# -------------------------------------------------------------- recolor ----


def grass_weight(h: np.ndarray, s: np.ndarray) -> np.ndarray:
    return (
        np.clip((s - SAT_RAMP[0]) / (SAT_RAMP[1] - SAT_RAMP[0]), 0, 1)
        * np.clip((h - HUE_RAMP_LO[0]) / (HUE_RAMP_LO[1] - HUE_RAMP_LO[0]), 0, 1)
        * np.clip((HUE_RAMP_HI[1] - h) / (HUE_RAMP_HI[1] - HUE_RAMP_HI[0]), 0, 1)
    )


def grass_core_medians(img: Image.Image) -> tuple[float, float, float]:
    im = np.asarray(img.convert("RGBA")).astype(float)
    h, s, v = vignette_rgb_to_hsv(im[..., :3] / 255.0)
    core = (im[..., 3] > 128) & (grass_weight(h, s) > CORE_W)
    return float(np.median(h[core])), float(np.median(s[core])), float(np.median(v[core]))


def plains_target(plains_dir: Path) -> tuple[float, float, float]:
    """The plains picks' grass-core HSV medians — the recolor target."""
    picks = sorted(plains_dir.glob("*.png"))
    if not picks:
        raise SystemExit(f"no plains picks in {plains_dir}")
    meds = np.array([grass_core_medians(Image.open(p)) for p in picks])
    t = np.median(meds, axis=0)
    return float(t[0]), float(t[1]), float(t[2])


def recolor_tile(img: Image.Image, target: tuple[float, float, float]) -> Image.Image:
    """Shift the tile's grass toward `target` (H additive, S/V multiplicative),
    weighted per-pixel by `grass_weight` so water and tan banks stay put."""
    im = np.asarray(img.convert("RGBA")).astype(float)
    h, s, v = vignette_rgb_to_hsv(im[..., :3] / 255.0)
    w = grass_weight(h, s)
    core = (im[..., 3] > 128) & (w > CORE_W)
    med_h, med_s, med_v = (float(np.median(x[core])) for x in (h, s, v))
    h2 = h + w * (target[0] - med_h)
    s2 = np.clip(s * (1 + w * (target[1] / med_s - 1)), 0, 1)
    v2 = np.clip(v * (1 + w * (target[2] / med_v - 1)), 0, 1)
    out_rgb = (hsv_to_rgb(h2, s2, v2) * 255).round().clip(0, 255)
    return Image.fromarray(np.dstack([out_rgb, im[..., 3]]).astype(np.uint8), "RGBA")


def run_recolor(args: argparse.Namespace) -> int:
    target = plains_target(args.plains)
    print(f"plains target: h {target[0]:.1f} s {target[1]:.2f} v {target[2]:.2f}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(args.in_dir.glob("*.png")):
        img = Image.open(p)
        med = grass_core_medians(img)
        recolor_tile(img, target).save(args.out_dir / p.name)
        print(
            f"  {p.name}: grass h {med[0]:.1f}->{target[0]:.1f} "
            f"s {med[1]:.2f}->{target[1]:.2f} v {med[2]:.2f}->{target[2]:.2f}"
        )
    return 0


# ---------------------------------------------------------------- build ----


def verify_against(out_dir: Path, ref_dir: Path) -> bool:
    """Byte-compare every tile in out_dir with its counterpart in ref_dir."""
    ok = True
    for out_path in sorted(out_dir.glob("*.png")):
        ref_path = ref_dir / out_path.name
        if not ref_path.exists():
            print(f"MISSING in reference: {ref_path}")
            ok = False
            continue
        a = np.asarray(Image.open(out_path).convert("RGBA"))
        b = np.asarray(Image.open(ref_path).convert("RGBA"))
        if a.shape != b.shape or not np.array_equal(a, b):
            diff = 0 if a.shape != b.shape else int((a != b).any(axis=-1).sum())
            print(f"DIFFERS: {out_path.name} ({diff} px)")
            ok = False
        else:
            print(f"identical: {out_path.name}")
    return ok


def run_build(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text())
    if args.section not in manifest:
        raise SystemExit(f"section {args.section!r} not in {args.manifest}")
    section = manifest[args.section]
    root = args.manifest.parent

    # the committed guide must match its recorded parameters (gen provenance)
    guide_cfg = section["layout_guide"]
    committed = np.asarray(Image.open(root / guide_cfg["file"]).convert("RGB"))
    rebuilt = np.asarray(
        build_guide(int(guide_cfg["win_px"]), float(guide_cfg["band_px"]), int(guide_cfg["seed"]))
    )
    if not np.array_equal(committed, rebuilt):
        print(f"GUIDE DIFFERS from recorded parameters: {guide_cfg['file']}")
        return 1
    print(f"guide reproduces from recorded parameters: {guide_cfg['file']}")

    cut_cfg = section["cut"]
    win = int(cut_cfg["win_px"])
    swatch = np.asarray(Image.open(root / section["swatch"]).convert("RGB"))
    target = plains_target(root / section["recolor"]["plains_picks"])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for w in cut_cfg["windows"]:
        tile = cut_window(swatch, int(w["x0"]), int(w["y0"]), win)
        recolor_tile(tile, target).save(args.out_dir / w["tile"])
        print(f"{w['tile']}: window ({w['x0']}, {w['y0']})+{win}")

    if args.verify:
        if not verify_against(args.out_dir, args.verify):
            return 1
        print("verify: all tiles byte-identical")
    return 0


# ------------------------------------------------------------------ CLI ----


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_plains = SCRIPT_DIR.parent.parent / "art" / "foundry" / "plains" / "picks"

    g = sub.add_parser("guide", help="node-straightened meander layout diagram")
    g.add_argument("--win", type=int, required=True, help="cut window px (sets the period)")
    g.add_argument("--band", type=float, required=True, help="band width, swatch px")
    g.add_argument("--seed", type=int, default=23)
    g.add_argument("--out", type=Path, required=True)

    c = sub.add_parser("cut", help="search a swatch for gate-passing hex windows")
    c.add_argument("swatch", type=Path)
    c.add_argument("--out-dir", type=Path, required=True)
    c.add_argument("--win", type=int, default=384, help="window px, downscaled to 128")
    c.add_argument("--top-n", type=int, default=6)
    c.add_argument("--plains", type=Path, default=default_plains, help="plains picks dir")

    r = sub.add_parser("recolor", help="palette-match grass toward the plains picks")
    r.add_argument("in_dir", type=Path)
    r.add_argument("out_dir", type=Path)
    r.add_argument("--plains", type=Path, default=default_plains, help="plains picks dir")

    b = sub.add_parser("build", help="manifest-driven replay of the ratified tiles")
    b.add_argument("--manifest", required=True, type=Path, help="art/foundry/manifest.json")
    b.add_argument("--section", default="river", help="manifest section (default river)")
    b.add_argument("--out-dir", required=True, type=Path)
    b.add_argument(
        "--verify",
        type=Path,
        help="byte-compare the output against this directory of ratified tiles",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cmd == "guide":
        img = build_guide(args.win, args.band, args.seed)
        img.save(args.out)
        print(f"wrote {args.out}  win {args.win}px, band {args.band:.0f}px, seed {args.seed}")
        return 0
    if args.cmd == "cut":
        return run_cut(args)
    if args.cmd == "recolor":
        return run_recolor(args)
    return run_build(args)


if __name__ == "__main__":
    sys.exit(main())
