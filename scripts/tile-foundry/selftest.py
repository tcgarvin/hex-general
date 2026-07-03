#!/usr/bin/env python3
"""Offline self-test for the tile-foundry toolkit — no network access.

Builds synthetic images in a temp directory and exercises `hex_mask`,
`cut.py`, `gate.py`'s seam/truncation gates (v1 smooth and v2 texture modes,
plus the `clique` subcommand), `patch.py`, `flatten.py`, and `pick-refs.py`'s
outlier penalty against known-good and known-bad cases. Run directly:

    python3 scripts/tile-foundry/selftest.py
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import cut  # noqa: E402
import flatten  # noqa: E402
import gate  # noqa: E402
import river  # noqa: E402
from gate import truncation_score  # noqa: E402
from hexgeom import axial_to_pixel, hex_mask, neighbor_pixel_offset, shared_edge  # noqa: E402

HEX_WIDTH = 128

RESULTS: list[tuple[str, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Record and print one assertion; returns `condition` for early-exit guards."""
    RESULTS.append((name, condition))
    status = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail and not condition else ""
    print(f"[{status}] {name}{suffix}")
    return condition


def run_cli(script: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of the tile-foundry CLI scripts as a real subprocess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_DIR / script), *args], capture_output=True, text=True
    )


def make_noise_swatch(path: Path, size: int, base: tuple[int, int, int], amplitude: int, seed: int) -> None:
    """A uniform-statistics RGB noise swatch: a base color plus small iid per-pixel noise."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(-amplitude, amplitude + 1, size=(size, size, 3))
    arr = np.clip(np.array(base) + noise, 0, 255).astype(np.uint8)
    Image.fromarray(arr, mode="RGB").save(path)


def make_noise_tile_array(hex_width: int, base: tuple[int, int, int], amplitude: int, seed: int) -> np.ndarray:
    """Like `make_noise_swatch` but returns an in-memory hex-masked RGBA tile directly."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(-amplitude, amplitude + 1, size=(hex_width, hex_width, 3))
    rgb = np.clip(np.array(base) + noise, 0, 255).astype(np.uint8)
    mask = hex_mask(hex_width, hex_width)
    return np.dstack([rgb, mask])


def make_bar_tile(path: Path, hex_width: int, bar_y_center: float, thickness: int = 3, half_len: int = 30) -> None:
    """A hex tile: flat mid-gray background plus a high-contrast horizontal bar.

    The bar is a stand-in for a salient feature (e.g. a rock edge or shadow
    line). Centering it at a hex edge's midpoint makes it straddle the mask
    boundary; centering it at the canvas centre keeps it safely interior.
    """
    mask = hex_mask(hex_width, hex_width)
    rgb = np.full((hex_width, hex_width, 3), 160, dtype=np.uint8)
    yy, xx = np.mgrid[0:hex_width, 0:hex_width]
    bar = (np.abs(yy - bar_y_center) <= thickness) & (np.abs(xx - hex_width / 2) <= half_len)
    rgb[bar] = 10
    rgba = np.dstack([rgb, mask])
    Image.fromarray(rgba, mode="RGBA").save(path)


def build_correlated_field(amplitude: float, radius: float, speckle_amp: float, seed: int, size: int) -> np.ndarray:
    """A stationary structured-texture RGB field: blurred low-frequency blobs plus fine speckle.

    Models a forest-canopy-like texture whose per-pixel interior std
    (sigma_tex) sits above the `--seam-mode auto` threshold, for exercising
    `--seam-mode texture` and the DC-step check. The low-frequency component
    is built by encoding noise into an 8-bit "L" image (scaled so ~4 std
    fits in 0..255 without much clipping), Gaussian-blurring it, then
    decoding back — PIL's blur only operates on integer-mode images.
    """
    rng = np.random.default_rng(seed)
    base = np.array([60, 110, 50])
    low_noise = rng.normal(0, amplitude, size=(size, size, 3))
    scale = 127.0 / (4 * amplitude)
    encoded = np.clip(128 + low_noise * scale, 0, 255).astype(np.uint8)
    blurred_encoded = np.stack(
        [
            np.asarray(
                Image.fromarray(encoded[..., c], mode="L").filter(ImageFilter.GaussianBlur(radius=radius)),
                dtype=np.float64,
            )
            for c in range(3)
        ],
        axis=-1,
    )
    blurred = (blurred_encoded - 128) / scale
    speckle = rng.normal(0, speckle_amp, size=(size, size, 3))
    return np.clip(base + blurred + speckle, 0, 255).astype(np.uint8)


def test_hex_mask_geometry() -> None:
    w = HEX_WIDTH
    mask = hex_mask(w, w)

    corner_alpha = max(int(mask[0, 0]), int(mask[0, w - 1]), int(mask[w - 1, 0]), int(mask[w - 1, w - 1]))
    check("hex_mask: corners ~0 alpha", corner_alpha < 5, f"corner_alpha={corner_alpha}")

    center_alpha = int(mask[w // 2, w // 2])
    check("hex_mask: center ~255 alpha", center_alpha > 250, f"center_alpha={center_alpha}")

    measured_area = float((mask.astype(np.float64) / 255.0).sum())
    expected_area = (3 * math.sqrt(3) / 8) * w * w
    rel_err = abs(measured_area - expected_area) / expected_area
    check(
        "hex_mask: area within 2% of (3*sqrt(3)/8)*W^2",
        rel_err < 0.02,
        f"measured={measured_area:.0f} expected={expected_area:.0f} rel_err={rel_err:.4f}",
    )


def test_cut_produces_gate_passing_tiles(tmp_dir: Path) -> None:
    swatch = tmp_dir / "noise_swatch.png"
    make_noise_swatch(swatch, 512, base=(120, 120, 120), amplitude=15, seed=1)
    out_dir = tmp_dir / "cand"

    result = run_cli("cut.py", str(swatch), "--out-dir", str(out_dir), "--hex-width", str(HEX_WIDTH))
    if not check("cut.py: exits 0 on a valid swatch", result.returncode == 0, result.stderr):
        return

    tiles = sorted(out_dir.glob("cand_*.png"))
    if not check("cut.py: wrote candidate tiles", len(tiles) > 0, f"count={len(tiles)}"):
        return

    tile_args = [str(t) for t in tiles[:6]]  # keep the O(n^2) gate run fast
    gate_result = run_cli("gate.py", *tile_args, "--hex-width", str(HEX_WIDTH))
    check(
        "gate.py: same-swatch noise tiles PASS seam+truncation gates",
        gate_result.returncode == 0,
        gate_result.stdout + gate_result.stderr,
    )


def test_seam_gate_fails_mismatched_colors(tmp_dir: Path) -> None:
    green_swatch = tmp_dir / "green_swatch.png"
    red_swatch = tmp_dir / "red_swatch.png"
    make_noise_swatch(green_swatch, HEX_WIDTH, base=(60, 150, 60), amplitude=6, seed=2)
    make_noise_swatch(red_swatch, HEX_WIDTH, base=(150, 60, 60), amplitude=6, seed=3)

    green_dir, red_dir = tmp_dir / "green", tmp_dir / "red"
    run_cli("cut.py", str(green_swatch), "--out-dir", str(green_dir), "--hex-width", str(HEX_WIDTH), "--limit", "1")
    run_cli("cut.py", str(red_swatch), "--out-dir", str(red_dir), "--hex-width", str(HEX_WIDTH), "--limit", "1")

    green_tiles = list(green_dir.glob("cand_*.png"))
    red_tiles = list(red_dir.glob("cand_*.png"))
    if not check("cut.py: produced a green and a red tile", len(green_tiles) == 1 and len(red_tiles) == 1):
        return

    result = run_cli(
        "gate.py", str(green_tiles[0]), str(red_tiles[0]), "--hex-width", str(HEX_WIDTH), "--json"
    )
    check(
        "gate.py: seam gate FAILS a green/red tile pair",
        result.returncode != 0,
        result.stdout + result.stderr,
    )


def test_truncation_gate(tmp_dir: Path) -> None:
    w = HEX_WIDTH
    straddling_path = tmp_dir / "straddling.png"
    centered_path = tmp_dir / "centered.png"

    # Direction 5 ((0, 1) in AXIAL_DIRECTIONS) is the hex's flat bottom edge —
    # a bar centred on its midpoint straddles the mask boundary along its length.
    (ex1, ey1), (ex2, ey2) = shared_edge(5, w)
    edge_mid_y = (ey1 + ey2) / 2 + w / 2
    make_bar_tile(straddling_path, w, bar_y_center=edge_mid_y)
    make_bar_tile(centered_path, w, bar_y_center=w / 2)

    straddling_arr = np.array(Image.open(straddling_path))
    centered_arr = np.array(Image.open(centered_path))
    straddling_score = truncation_score(straddling_arr, w)
    centered_score = truncation_score(centered_arr, w)

    check(
        "truncation_score: higher for a boundary-straddling bar than a centred one",
        straddling_score > centered_score,
        f"straddling={straddling_score:.3f} centered={centered_score:.3f}",
    )
    check(
        "gate.py: truncation gate FAILS the straddling bar (score > 1.5)",
        straddling_score > 1.5,
        f"score={straddling_score:.3f}",
    )
    check(
        "gate.py: truncation gate PASSES the centred bar (score <= 1.5)",
        centered_score <= 1.5,
        f"score={centered_score:.3f}",
    )

    # Cross-check via the real CLI too, not just the imported function.
    result = run_cli("gate.py", str(centered_path), "--hex-width", str(w))
    check("gate.py CLI: PASSES a tile with a centred bar", result.returncode == 0, result.stdout + result.stderr)
    result = run_cli("gate.py", str(straddling_path), "--hex-width", str(w))
    check("gate.py CLI: FAILS a tile with a boundary-straddling bar", result.returncode != 0, result.stdout)


def test_bleed_grows_mask_area(tmp_dir: Path) -> None:
    m0 = cut.bleed_mask(hex_mask(HEX_WIDTH, HEX_WIDTH), 0)
    m1 = cut.bleed_mask(hex_mask(HEX_WIDTH, HEX_WIDTH), 1)
    m2 = cut.bleed_mask(hex_mask(HEX_WIDTH, HEX_WIDTH), 2)
    a0, a1, a2 = (float(m.astype(np.float64).sum() / 255.0) for m in (m0, m1, m2))
    check("bleed_mask: --bleed 1 grows the opaque area vs --bleed 0", a1 > a0, f"a0={a0:.0f} a1={a1:.0f}")
    check("bleed_mask: --bleed 2 grows the opaque area further", a2 > a1, f"a1={a1:.0f} a2={a2:.0f}")

    swatch = tmp_dir / "bleed_swatch.png"
    make_noise_swatch(swatch, 256, base=(100, 140, 90), amplitude=10, seed=5)
    no_bleed_dir, bleed_dir = tmp_dir / "no_bleed", tmp_dir / "bleed"
    run_cli("cut.py", str(swatch), "--out-dir", str(no_bleed_dir), "--hex-width", str(HEX_WIDTH), "--limit", "1", "--bleed", "0")
    run_cli("cut.py", str(swatch), "--out-dir", str(bleed_dir), "--hex-width", str(HEX_WIDTH), "--limit", "1", "--bleed", "2")

    no_bleed_tiles = list(no_bleed_dir.glob("cand_*.png"))
    bleed_tiles = list(bleed_dir.glob("cand_*.png"))
    if not check(
        "cut.py --bleed: produced both a 0-bleed and a 2-bleed candidate",
        len(no_bleed_tiles) == 1 and len(bleed_tiles) == 1,
    ):
        return

    no_bleed_area = float(np.array(Image.open(no_bleed_tiles[0]))[..., 3].astype(np.float64).sum() / 255.0)
    bleed_area = float(np.array(Image.open(bleed_tiles[0]))[..., 3].astype(np.float64).sum() / 255.0)
    check(
        "cut.py: --bleed 2 candidate has a larger opaque area than --bleed 0",
        bleed_area > no_bleed_area,
        f"no_bleed_area={no_bleed_area:.0f} bleed_area={bleed_area:.0f}",
    )


def test_dc_step_and_texture_mode() -> None:
    hw = HEX_WIDTH
    field = build_correlated_field(amplitude=300, radius=4, speckle_amp=6, seed=42, size=512)
    mask = hex_mask(hw, hw)

    # Anchor tile A at (cx, cy); tile B is cropped at the EXACT axial offset
    # gate.py itself uses for direction 0, so the two really are continuous
    # across that one seam (unlike other directions, which are uncontrolled
    # for this pair and not exercised here).
    cx, cy = 180, 180
    direction = 0
    ox, oy = neighbor_pixel_offset(direction, hw)
    bx, by = int(round(cx + ox)), int(round(cy + oy))
    tile_a = np.dstack([field[cy : cy + hw, cx : cx + hw], mask])
    tile_b = np.dstack([field[by : by + hw, bx : bx + hw], mask])

    sigma_tex = (gate.interior_luminance_std(tile_a, hw) + gate.interior_luminance_std(tile_b, hw)) / 2.0
    check(
        "interior_luminance_std: synthetic canopy sigma_tex exceeds the auto-mode threshold",
        sigma_tex > gate.SEAM_MODE_AUTO_SIGMA_THRESHOLD,
        f"sigma_tex={sigma_tex:.2f}",
    )

    a_rgb = tile_a[..., :3].astype(np.float64)
    b_rgb = tile_b[..., :3].astype(np.float64)

    smooth_ratio = gate.seam_ratio(a_rgb, b_rgb, direction, hw)
    baseline = max(gate.decorrelated_baseline(a_rgb, hw), gate.SEAM_BASELINE_FLOOR)
    texture_ratio = gate.seam_ratio_texture(a_rgb, b_rgb, direction, hw, baseline)
    check(
        "seam_ratio_texture: decorrelated baseline scores a real seamless canopy join far below the v1 local-baseline ratio",
        texture_ratio < smooth_ratio,
        f"smooth={smooth_ratio:.2f} texture={texture_ratio:.2f}",
    )

    dc_threshold = gate.DEFAULT_DC_K * sigma_tex
    dc_natural = gate.dc_step(a_rgb, b_rgb, direction, hw)
    check(
        "dc_step: a real seamless canopy join stays within the DC-step tolerance",
        abs(dc_natural) <= dc_threshold,
        f"dc={dc_natural:.2f} threshold={dc_threshold:.2f}",
    )

    b_rgb_stepped = np.clip(b_rgb + 40.0, 0, 255)
    dc_stepped = gate.dc_step(a_rgb, b_rgb_stepped, direction, hw)
    check(
        "dc_step: a deliberately brightness-stepped pair exceeds the DC-step tolerance",
        abs(dc_stepped) > dc_threshold,
        f"dc={dc_stepped:.2f} threshold={dc_threshold:.2f}",
    )


def test_clique_finds_planted_subset(tmp_dir: Path) -> None:
    hw = HEX_WIDTH
    clique_dir = tmp_dir / "clique_tiles"
    clique_dir.mkdir()

    good_names = []
    for i in range(4):
        arr = make_noise_tile_array(hw, base=(120, 120, 120), amplitude=8, seed=100 + i)
        name = f"good_{i}.png"
        Image.fromarray(arr, mode="RGBA").save(clique_dir / name)
        good_names.append(name)
    for i, base in enumerate([(200, 40, 40), (40, 40, 200)]):
        arr = make_noise_tile_array(hw, base=base, amplitude=8, seed=200 + i)
        Image.fromarray(arr, mode="RGBA").save(clique_dir / f"odd_{i}.png")

    paths = sorted(clique_dir.glob("*.png"))
    tiles = [(p, np.array(Image.open(p))) for p in paths]
    _, seam_results = gate.run_seam_gate(tiles, hw, seam_max=2.0, seam_mode="smooth", dc_k=gate.DEFAULT_DC_K)
    names = [p.name for p in paths]
    compat = gate.build_compatibility_graph(names, seam_results)
    best = gate.largest_clique_all_seeds(compat)
    check(
        "gate.py clique: largest_clique_all_seeds finds exactly the planted 4-tile compatible subset",
        set(best) == set(good_names),
        f"found={sorted(best)}",
    )

    out_dir = tmp_dir / "clique_picked"
    result = run_cli(
        "gate.py",
        "clique",
        str(clique_dir / "*.png"),
        "--hex-width",
        str(hw),
        "--seam-mode",
        "smooth",
        "--pick",
        "3",
        "--out-dir",
        str(out_dir),
    )
    if not check("gate.py clique --pick: exits 0", result.returncode == 0, result.stdout + result.stderr):
        return

    picked = sorted(out_dir.glob("0*.png"))
    check("gate.py clique --pick 3: wrote exactly 3 numbered tiles", len(picked) == 3, f"count={len(picked)}")

    manifest_path = out_dir / "manifest.json"
    if not check("gate.py clique --pick: wrote a manifest.json", manifest_path.is_file()):
        return
    manifest = json.loads(manifest_path.read_text())
    sources = {t["source"] for t in manifest["tiles"]}
    check(
        "gate.py clique --pick 3: all 3 winners come from the planted compatible subset",
        sources.issubset(set(good_names)),
        f"sources={sources}",
    )


def test_patch_produces_expected_outputs(tmp_dir: Path) -> None:
    hw = HEX_WIDTH
    mask = hex_mask(hw, hw)
    primary_dir, context_dir = tmp_dir / "patch_primary", tmp_dir / "patch_context"
    primary_dir.mkdir()
    context_dir.mkdir()
    for i in range(2):
        rgb = np.full((hw, hw, 3), 60 + i * 20, dtype=np.uint8)
        Image.fromarray(np.dstack([rgb, mask]), mode="RGBA").save(primary_dir / f"{i:02d}.png")
    for i in range(2):
        rgb = np.full((hw, hw, 3), 180 + i * 20, dtype=np.uint8)
        Image.fromarray(np.dstack([rgb, mask]), mode="RGBA").save(context_dir / f"{i:02d}.png")

    out_prefix = tmp_dir / "patch_preview" / "preview"
    result = run_cli(
        "patch.py",
        "--tiles",
        str(primary_dir),
        "--context",
        str(context_dir),
        "--layout",
        "mass",
        "--hex-width",
        str(hw),
        "--out",
        str(out_prefix),
    )
    if not check("patch.py: exits 0", result.returncode == 0, result.stdout + result.stderr):
        return

    full_path = out_prefix.parent / f"{out_prefix.name}-full.png"
    board_path = out_prefix.parent / f"{out_prefix.name}-board.png"
    if not check("patch.py: wrote both -full.png and -board.png", full_path.is_file() and board_path.is_file()):
        return

    full_img = Image.open(full_path)
    board_img = Image.open(board_path)

    size = hw / 2.0
    cols, rows = 8, 5
    positions = [axial_to_pixel(q, r, size) for q in range(cols) for r in range(rows)]
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    expected_w = int(math.ceil(max(xs) - min(xs) + hw))
    expected_h = int(math.ceil(max(ys) - min(ys) + hw))
    check(
        "patch.py: full-res canvas matches the expected 8x5 hex bounding box",
        full_img.size == (expected_w, expected_h),
        f"got={full_img.size} expected=({expected_w},{expected_h})",
    )

    scale = 50.0 / hw
    expected_board = (max(1, round(expected_w * scale)), max(1, round(expected_h * scale)))
    check(
        "patch.py: board-scale PNG matches the 50px/hex resize of the full canvas",
        board_img.size == expected_board,
        f"got={board_img.size} expected={expected_board}",
    )


def test_flatten_reduces_gradient_spread(tmp_dir: Path) -> None:
    size = 512
    rng = np.random.default_rng(3)
    xs = np.linspace(-80, 80, size)
    gradient = np.tile(xs, (size, 1))
    texture_noise = rng.integers(-10, 11, size=(size, size))
    luminance_plane = np.clip(128 + gradient + texture_noise, 0, 255)
    rgb = np.stack([luminance_plane] * 3, axis=-1).astype(np.uint8)

    swatch_path = tmp_dir / "gradient_swatch.png"
    Image.fromarray(rgb, mode="RGB").save(swatch_path)

    out_path = tmp_dir / "gradient_flat.png"
    result = run_cli("flatten.py", str(swatch_path), "--out", str(out_path))
    if not check("flatten.py: exits 0", result.returncode == 0, result.stdout + result.stderr):
        return
    if not check("flatten.py: wrote the output PNG", out_path.is_file()):
        return

    flattened = np.array(Image.open(out_path).convert("RGB"))
    before_std = float(flatten.luminance_of(rgb.astype(np.float64)).std())
    after_std = float(flatten.luminance_of(flattened.astype(np.float64)).std())
    check(
        "flatten.py: reduces the synthetic gradient's luminance-field spread substantially",
        after_std < before_std * 0.5,
        f"before={before_std:.2f} after={after_std:.2f}",
    )


def test_pick_refs_penalizes_red_blob(tmp_dir: Path) -> None:
    img = np.zeros((512, 512, 3), dtype=np.uint8)
    img[..., 0] = 60
    img[..., 1] = 150
    img[..., 2] = 60
    img[256 + 50 : 256 + 150, 256 + 50 : 256 + 150] = (255, 0, 0)  # planted reticle-like blob
    concept_path = tmp_dir / "concept.png"
    Image.fromarray(img, mode="RGB").save(concept_path)

    out_dir = tmp_dir / "refs"
    result = run_cli(
        "pick-refs.py", str(concept_path), "--out-dir", str(out_dir), "--window", "256", "--stride", "256", "--k", "4"
    )
    if not check("pick-refs.py: exits 0 with a planted red blob", result.returncode == 0, result.stderr):
        return

    scores: dict[tuple[int, int], float] = {}
    for line in result.stdout.splitlines():
        if not line.startswith("#"):
            continue
        parts = line.split()
        score = float(parts[1].split("=")[1])
        x = int(parts[2].split("=")[1])
        y = int(parts[3].split("=")[1])
        scores[(x, y)] = score

    contaminated_score = scores.get((256, 256))
    clean_scores = [s for (x, y), s in scores.items() if (x, y) != (256, 256)]
    check(
        "pick-refs.py: contaminated window scores below every clean window",
        contaminated_score is not None and bool(clean_scores) and all(contaminated_score < s for s in clean_scores),
        f"scores={scores}",
    )


def test_gate_summary_stays_short(tmp_dir: Path) -> None:
    summary_dir = tmp_dir / "summary_tiles"
    summary_dir.mkdir()
    for i in range(4):
        arr = make_noise_tile_array(HEX_WIDTH, base=(120, 120, 120), amplitude=8, seed=300 + i)
        Image.fromarray(arr, mode="RGBA").save(summary_dir / f"good_{i}.png")
    for i, base in enumerate([(200, 40, 40), (40, 40, 200)]):
        arr = make_noise_tile_array(HEX_WIDTH, base=base, amplitude=8, seed=400 + i)
        Image.fromarray(arr, mode="RGBA").save(summary_dir / f"odd_{i}.png")

    pattern = str(summary_dir / "*.png")
    full = run_cli("gate.py", pattern, "--hex-width", str(HEX_WIDTH), "--seam-mode", "smooth")
    summary = run_cli("gate.py", pattern, "--hex-width", str(HEX_WIDTH), "--seam-mode", "smooth", "--summary")

    full_lines = full.stdout.strip().splitlines()
    summary_lines = summary.stdout.strip().splitlines()
    check(
        "gate.py --summary: output is much shorter than the full report for a mixed-pass/fail batch",
        len(summary_lines) < len(full_lines) and len(summary_lines) <= 30,
        f"full={len(full_lines)} summary={len(summary_lines)}",
    )
    check(
        "gate.py --summary: still ends with a PASS/FAIL verdict",
        bool(summary_lines) and summary_lines[-1] in ("PASS", "FAIL"),
        f"last_line={summary_lines[-1] if summary_lines else '<empty>'}",
    )


def test_compose_matches_ratified_woods_tiles(tmp_dir: Path) -> None:
    """Reproduce the applied woods tiles byte-for-byte via `compose.py --verify`.

    This is the compositor's acceptance test: it depends on the real (large,
    generated) sprite sheet and ratified plains base under `art/foundry/`, and
    on the applied output under `frontend/src/board/tiles/woods/` — both
    absent in some environments (e.g. a shallow checkout), so this skips
    gracefully rather than failing when they're missing.
    """
    repo_root = SCRIPT_DIR.parent.parent
    manifest_path = repo_root / "art" / "foundry" / "manifest.json"
    ref_dir = repo_root / "frontend" / "src" / "board" / "tiles" / "woods"
    if not manifest_path.is_file() or not ref_dir.is_dir():
        print(f"[SKIP] compose.py: ratified-tile check (missing {manifest_path} or {ref_dir})")
        return

    out_dir = tmp_dir / "compose_ratified"
    result = run_cli(
        "compose.py",
        "--manifest",
        str(manifest_path),
        "--section",
        "woods",
        "--out-dir",
        str(out_dir),
        "--verify",
        str(ref_dir),
    )
    check(
        "compose.py: reproduces frontend/src/board/tiles/woods/*.png byte-for-byte (--verify)",
        result.returncode == 0,
        result.stdout + result.stderr,
    )


def test_vignette_matches_ratified_tiles(tmp_dir: Path) -> None:
    """Reproduce the applied hills, city, and town tiles byte-for-byte via
    `vignette.py --verify`.

    Same acceptance-test shape as the woods check above: depends on the real
    sprite sheets and ratified plains bases under `art/foundry/` and the
    applied output under `frontend/src/board/tiles/`, so it skips gracefully
    when those are absent.
    """
    repo_root = SCRIPT_DIR.parent.parent
    manifest_path = repo_root / "art" / "foundry" / "manifest.json"
    for section in ["hills", "city", "town"]:
        ref_dir = repo_root / "frontend" / "src" / "board" / "tiles" / section
        if not manifest_path.is_file() or not ref_dir.is_dir():
            print(f"[SKIP] vignette.py: ratified-tile check for {section} (missing inputs)")
            continue
        out_dir = tmp_dir / f"vignette_ratified_{section}"
        result = run_cli(
            "vignette.py",
            "--manifest",
            str(manifest_path),
            "--section",
            section,
            "--out-dir",
            str(out_dir),
            "--verify",
            str(ref_dir),
        )
        check(
            f"vignette.py: reproduces frontend/src/board/tiles/{section}/*.png byte-for-byte (--verify)",
            result.returncode == 0,
            result.stdout + result.stderr,
        )


def test_river_matches_ratified_tiles(tmp_dir: Path) -> None:
    """Reproduce the applied river straight_v tiles byte-for-byte via
    `river.py build --verify`.

    Same acceptance-test shape as the woods/hills checks above: depends on the
    committed swatch + guide under `art/foundry/river/` and the applied tiles
    under `frontend/src/board/tiles/river/`, so it skips gracefully when those
    are absent. `build` also re-derives the layout guide from its recorded
    parameters and fails if the committed guide drifted.
    """
    repo_root = SCRIPT_DIR.parent.parent
    manifest_path = repo_root / "art" / "foundry" / "manifest.json"
    ref_dir = repo_root / "frontend" / "src" / "board" / "tiles" / "river"
    if not manifest_path.is_file() or not ref_dir.is_dir():
        print(f"[SKIP] river.py: ratified-tile check (missing {manifest_path} or {ref_dir})")
        return

    out_dir = tmp_dir / "river_ratified"
    result = run_cli(
        "river.py",
        "build",
        "--manifest",
        str(manifest_path),
        "--section",
        "river",
        "--out-dir",
        str(out_dir),
        "--verify",
        str(ref_dir),
    )
    check(
        "river.py: reproduces frontend/src/board/tiles/river/straight_v_*.png byte-for-byte (--verify)",
        result.returncode == 0,
        result.stdout + result.stderr,
    )


def test_river_ends_guide_audits_clean() -> None:
    """The `ends` guide builds and every anchor measures as its 1-edge shape.

    `audit_tree_guide` runs the real swatch cut gates (crossing + containment +
    the new terminus gate) at each anchor and raises on any failure — the free
    correctness check for the whole end-cap spec, no NBP spend required.
    """
    win, band = 384, 40.0
    img, anchors = river.build_tree_guide("ends", win, band)
    names = sorted(a["shape"] for a in anchors)
    if not check(
        "river.py ends: guide yields the four end-cap anchors",
        names == ["end_n", "end_ne", "end_s", "end_se"],
        f"anchors={names}",
    ):
        return
    detail = ""
    try:
        river.audit_tree_guide(img, anchors, win)
        audited = True
    except RuntimeError as exc:
        audited, detail = False, str(exc)
    check("river.py ends: audit_tree_guide passes all four anchors", audited, detail)


def test_river_end_shape_tables() -> None:
    """SHAPES/ORIENTED_TILES 1-edge entries are self-consistent and obey the mirror."""
    end_shapes = {s: e for s, e in river.SHAPES.items() if s.startswith("end_")}
    check(
        "river.py: end_* shapes are all exactly 1-edge",
        len(end_shapes) == 4 and all(len(e) == 1 for e in end_shapes.values()),
        f"end_shapes={end_shapes}",
    )

    singles = {k: v for k, v in river.ORIENTED_TILES.items() if len(k) == 1}
    check(
        "river.py: all six 1-edge masks map to an end tile",
        set(singles) == {(0,), (1,), (2,), (3,), (4,), (5,)},
        f"masks={sorted(singles)}",
    )

    # M swaps 0<->4 and 1<->3, fixing 2 and 5. A registered mask's edge must be
    # its base shape's edge (unmirrored) or its mirror image (mirrored).
    mirror = {0: 4, 1: 3, 2: 2, 3: 1, 4: 0, 5: 5}
    consistent = True
    for (edge,), (shape, flag) in singles.items():
        base_edge = river.SHAPES[shape][0]
        expect = mirror[base_edge] if flag else base_edge
        consistent = consistent and expect == edge
    check(
        "river.py: 1-edge ORIENTED_TILES obey the mirror convention (0<->4, 1<->3)",
        consistent,
        f"singles={singles}",
    )
    check(
        "river.py: end_n and end_s straddle the mirror axis (never flagged)",
        singles[(2,)] == ("end_n", False) and singles[(5,)] == ("end_s", False),
        f"n={singles[(2,)]} s={singles[(5,)]}",
    )


def test_river_measure_shape_end_terminus() -> None:
    """measure_shape accepts the v2 snake head and rejects too-deep water.

    Bracket the terminus gate: the real spring head (tip ~0.24*win past the
    centre) passes; a straight band ending 0.36*win past the centre — deeper
    than the 0.30*win limit but far short of the opposite edge — fails on the
    terminus gate specifically; a full straight-through fails too.
    """
    win, band = 384, 40.0
    gates = river.Gates(win)
    hd = river.hex_boundary_dist(win)
    ring = (hd >= 0) & (hd <= 6.0)
    cx = cy = river.GUIDE_SIZE // 2

    pts, radii = river._end_head((cx, cy), river._ray_end((cx, cy), 2, river.TREE_RAY), band, win)
    water_cap = np.asarray(river._stamp_band(pts, band, radii).convert("L")) == 128
    m_cap = river.measure_shape(water_cap, cx, cy, "end_n", gates, ring)
    check(
        "river.py measure_shape: the v2 snake head measures as end_n, tip past the centre",
        m_cap is not None and 0 < m_cap["terminus"] <= river.T_TERMINUS_MAX * win,
        f"m={m_cap}",
    )

    # over-deep band: full width, ending 0.36*win past the centre (inside the
    # hex, so containment and the crossing gates all pass — only terminus trips)
    ux, uy = river._unit(river.EDGE_THETA_DEG[2])
    deep_end = (cx + 0.36 * win * -ux, cy + 0.36 * win * -uy)
    deep = river._straight_samples(river._ray_end((cx, cy), 2, river.TREE_RAY), deep_end)
    water_deep = np.asarray(river._stamp_band(deep, band).convert("L")) == 128
    m_deep = river.measure_shape(water_deep, cx, cy, "end_n", gates, ring)
    term_deep = river.terminus_offset(water_deep, cx, cy, 2, gates)
    check(
        "river.py measure_shape: a band ending past the 0.30*win limit FAILS the terminus gate",
        m_deep is None and term_deep > river.T_TERMINUS_MAX * win,
        f"m={m_deep} terminus={term_deep:.1f}",
    )

    thru = river._straight_samples(
        river._ray_end((cx, cy), 5, river.TREE_RAY), river._ray_end((cx, cy), 2, river.TREE_RAY)
    )
    water_thru = np.asarray(river._stamp_band(thru, band).convert("L")) == 128
    m_thru = river.measure_shape(water_thru, cx, cy, "end_n", gates, ring)
    check(
        "river.py measure_shape: a straight-through band FAILS the end_n terminus gate",
        m_thru is None,
        f"m={m_thru}",
    )


def make_synthetic_tree_sheet(path: Path) -> None:
    """A small white-background sheet with a round blob (deciduous, h/w ~1.0) and a
    tall blob (conifer, h/w > 1.15), each well over compose.py's 400px min blob area.
    """
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([10, 10, 60, 60], fill=(40, 120, 40))
    draw.ellipse([100, 5, 130, 95], fill=(20, 80, 30))
    img.save(path)


def make_synthetic_manifest(tmp_dir: Path, seeds: list[int]) -> Path:
    """A minimal manifest.json + sprite sheet + plains base tiles, so `compose.py`'s
    determinism can be checked offline without depending on the real (large) art assets.
    """
    manifest_dir = tmp_dir / "synthetic_manifest"
    plains_dir = manifest_dir / "plains"
    plains_dir.mkdir(parents=True)

    make_synthetic_tree_sheet(manifest_dir / "sheet.png")

    tile_px = 64
    mask = hex_mask(tile_px, tile_px)
    for v in range(len(seeds)):
        base_rgb = np.full((tile_px, tile_px, 3), 90, dtype=np.uint8)
        Image.fromarray(np.dstack([base_rgb, mask]), mode="RGBA").save(plains_dir / f"{v:02d}.png")

    manifest = {
        "tile_spec": {"canvas_px": tile_px},
        "woods": {
            "sprite_sheet": "sheet.png",
            "compose": {
                "base": "plains/{v}.png (synthetic base, selftest only)",
                "seeds": seeds,
                "target_trees": 6,
                "max_attempts": 3000,
                "conifer_quota": 0.35,
                "tree_height_px": [6, 10],
            },
            "sprite_processing": {
                "target_mean_luminance": {"deciduous": 110.0, "conifer": 80.0},
                "desaturation": 0.9,
            },
        },
    }
    manifest_path = manifest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path


def test_compose_determinism(tmp_dir: Path) -> None:
    manifest_path = make_synthetic_manifest(tmp_dir, seeds=[42, 43])
    out1, out2 = tmp_dir / "compose_run1", tmp_dir / "compose_run2"

    result1 = run_cli("compose.py", "--manifest", str(manifest_path), "--section", "woods", "--out-dir", str(out1))
    result2 = run_cli("compose.py", "--manifest", str(manifest_path), "--section", "woods", "--out-dir", str(out2))
    if not check(
        "compose.py: two runs of a synthetic manifest both exit 0",
        result1.returncode == 0 and result2.returncode == 0,
        result1.stdout + result1.stderr + result2.stdout + result2.stderr,
    ):
        return

    tiles1 = sorted(out1.glob("*.png"))
    tiles2 = sorted(out2.glob("*.png"))
    if not check(
        "compose.py determinism: both runs wrote the same set of tile files",
        len(tiles1) > 0 and [p.name for p in tiles1] == [p.name for p in tiles2],
    ):
        return

    all_identical = all(
        np.array_equal(np.array(Image.open(p1)), np.array(Image.open(p2))) for p1, p2 in zip(tiles1, tiles2)
    )
    check("compose.py determinism: same-seed renders are pixel-identical across runs", all_identical)

    placements1 = json.loads((out1 / "placements.json").read_text())
    placements2 = json.loads((out2 / "placements.json").read_text())
    check("compose.py determinism: placements.json is identical across runs", placements1 == placements2)


def test_cli_help() -> None:
    for script in [
        "gen.py",
        "cut.py",
        "gate.py",
        "pick-refs.py",
        "patch.py",
        "flatten.py",
        "compose.py",
        "vignette.py",
        "river.py",
    ]:
        result = run_cli(script, "--help")
        check(f"{script} --help parses", result.returncode == 0, result.stderr)
    result = run_cli("gate.py", "clique", "--help")
    check("gate.py clique --help parses", result.returncode == 0, result.stderr)


def main() -> int:
    test_hex_mask_geometry()
    test_dc_step_and_texture_mode()
    test_river_ends_guide_audits_clean()
    test_river_end_shape_tables()
    test_river_measure_shape_end_terminus()
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        test_cut_produces_gate_passing_tiles(tmp_dir)
        test_seam_gate_fails_mismatched_colors(tmp_dir)
        test_truncation_gate(tmp_dir)
        test_bleed_grows_mask_area(tmp_dir)
        test_clique_finds_planted_subset(tmp_dir)
        test_patch_produces_expected_outputs(tmp_dir)
        test_flatten_reduces_gradient_spread(tmp_dir)
        test_pick_refs_penalizes_red_blob(tmp_dir)
        test_gate_summary_stays_short(tmp_dir)
        test_compose_matches_ratified_woods_tiles(tmp_dir)
        test_compose_determinism(tmp_dir)
        test_vignette_matches_ratified_tiles(tmp_dir)
        test_river_matches_ratified_tiles(tmp_dir)
    test_cli_help()

    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
