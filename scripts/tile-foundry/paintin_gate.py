"""Automatic per-tile gates for `paintin.py` tiles.

Each check carries the measured value and its limit. Hard checks fail the tile;
advisory checks (luminance / hue against concept anchors) are reported only.

    registration    centre residual (tile px) and silhouette IoU of the cell
    rim_exact       final RGB == deterministic target over the rim band (the
                    base, plus the crossing profile at connected edges)
    rim_clear       non-oriented: share of salient painted pixels in the pinned
                    rim band of the *cut* (a feature spilling over the rim)
    rim_touch       non-oriented: salient pixels within a few px of the boundary
                    (a single sliced tree/building)
    cross_cut_*     oriented, per connected edge: Codex's crossing centre offset
                    and width at the hand-over depth, before pinning
    cross_final_*   oriented, per connected edge: crossing centre/width at the
                    edge in the final tile (checks the pin itself)
    leak            oriented: feature pixels touching the rim away from the
                    connected crossings (cut and final)
    lum / hue       advisory: alpha-weighted mean luminance and median hue vs
                    the job's anchors
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

import numpy as np

from paintin_geom import (
    Guide,
    edge_frame,
    max_filter,
    min_filter,
    pixel_grid,
    tile_distance,
)
from paintin_pin import (
    CrossProfile,
    PinConfig,
    diff_mask,
    feature_mask,
    hsv,
    measure_crossing,
    paint_region,
)
from paintin_sheet import CellFit


PAINT_OVER_CLOSE_PX = 3  # closing radius for a road painted over relief (see _oriented_checks)


@dataclass(frozen=True)
class GateConfig:
    """Gate limits (tile px unless noted); a job's `gates` object overrides them."""

    max_residual_tile_px: float = 0.75
    min_iou: float = 0.97
    rim_salient_max: float = 0.03  # fraction of the rim band
    rim_touch_px: float = 4.0  # salient pixels this close to the boundary...
    rim_touch_max: int = 2  # ...may number at most this (catches one sliced tree)
    cross_centre_max_cut: float = 3.0
    cross_width_rel_tol_cut: float = 0.35
    cross_centre_max_final: float = 0.75
    cross_width_tol_final: float = 2.0
    leak_band_px: float = 8.0
    leak_max_px: int = 4
    lum_tol: float = 8.0
    hue_tol: float = 6.0
    feature_lum_tol: float = 6.0
    feature_sat_tol: float = 0.05

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> GateConfig:
        """Build from a job's `gates` object; unknown keys raise."""
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"unknown gate settings {sorted(unknown)}; known: {sorted(known)}"
            )
        return cls(**raw)


@dataclass(frozen=True)
class GateCheck:
    """One measured check."""

    name: str
    value: float
    limit: float
    passed: bool
    hard: bool
    detail: str

    def line(self) -> str:
        """One-line human summary."""
        tag = "ok  " if self.passed else ("FAIL" if self.hard else "warn")
        return f"{tag} {self.name}: {self.detail}"


def _check(
    name: str, value: float, limit: float, ok: bool, detail: str, hard: bool = True
):
    return GateCheck(name, round(float(value), 4), float(limit), bool(ok), hard, detail)


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec. 601 luma of a float RGB array."""
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def tile_colour_stats(rgba: np.ndarray) -> dict[str, float]:
    """Alpha-weighted mean luminance and median hue/saturation over the hex."""
    rgb = rgba[..., :3].astype(np.float64)
    a = rgba[..., 3].astype(np.float64) / 255.0
    lum = float((luminance(rgb) * a).sum() / a.sum())
    hue, sat, _ = hsv(rgb)
    inside = a > 0.5
    return {
        "lum": round(lum, 2),
        "hue": round(float(np.median(hue[inside])), 2),
        "sat": round(float(np.median(sat[inside])), 3),
    }


def crossing_zone(guide: Guide, profile: CrossProfile, band: float) -> np.ndarray:
    """Pixels within `band` of a connected edge and laterally inside its profile span."""
    xs, ys = pixel_grid(128)
    zone = np.zeros((128, 128), dtype=bool)
    lateral = profile.core_half + profile.feather + 2.0
    for edge in guide.edges:
        depth, t = edge_frame(edge, xs, ys)
        zone |= (depth <= band + 1.0) & (np.abs(t) <= lateral)
    for edge in guide.hold_edges:  # the base's own crossing (bridges)
        depth, t = edge_frame(edge, xs, ys)
        zone |= (depth <= band + 1.0) & (np.abs(t) <= guide.hold_half + 2.0)
    return zone


def feature_colour_stats(rgba: np.ndarray, base: np.ndarray, kind: str) -> dict[str, float]:
    """Median luminance / saturation / RGB of a tile's feature core (classifier
    mask > 0.5, more than 3 px inside the hex), and the base ground's median
    luminance for the contrast step."""
    rgb = rgba[..., :3].astype(np.float64)
    core = (feature_mask(kind, rgb, base) > 0.5) & (tile_distance() > 3.0)
    if not core.any():
        return {"px": 0}
    _, sat, _ = hsv(rgb[core])
    lum = luminance(rgb[core])
    ground = luminance(base)[tile_distance() > 3.0]
    med = np.median(rgb[core], axis=0)
    return {
        "px": int(core.sum()),
        "lum": round(float(np.median(lum)), 1),
        "sat": round(float(np.median(sat)), 3),
        "rgb": [round(float(v), 1) for v in med],
        "ground_lum": round(float(np.median(ground)), 1),
        "delta_lum": round(float(np.median(lum) - np.median(ground)), 1),
    }


def gate_cell(
    fit: CellFit,
    cut: np.ndarray,
    base: np.ndarray,
    final: np.ndarray,
    target: np.ndarray,
    guide: Guide | None,
    profile: CrossProfile | None,
    pin: PinConfig,
    cfg: GateConfig,
    anchors: dict[str, float],
) -> list[GateCheck]:
    """Run every gate for one tile. `final` is the uint8 RGBA tile."""
    checks: list[GateCheck] = []
    checks.append(
        _check(
            "registration_residual",
            fit.residual_tile_px,
            cfg.max_residual_tile_px,
            fit.residual_tile_px <= cfg.max_residual_tile_px,
            f"centre residual {fit.residual_tile_px:.2f} tile px "
            f"(limit {cfg.max_residual_tile_px})",
        )
    )
    checks.append(
        _check(
            "registration_iou",
            fit.iou,
            cfg.min_iou,
            fit.iou >= cfg.min_iou,
            f"silhouette IoU {fit.iou:.3f} (min {cfg.min_iou})",
        )
    )
    d = tile_distance()
    alpha = final[..., 3]
    rim = (alpha > 0) & (d < pin.rim_exact_px)
    final_rgb = final[..., :3].astype(np.float64)
    diff = np.abs(final_rgb[rim] - np.clip(np.round(target[rim]), 0, 255))
    worst = float(diff.max()) if diff.size else 0.0
    checks.append(
        _check(
            "rim_exact",
            worst,
            0.0,
            worst == 0.0,
            f"max |final - target| {worst:.0f} over {int(rim.sum())} rim px "
            f"(d < {pin.rim_exact_px})",
        )
    )
    band = (d >= 0) & (d < pin.rim_exact_px + pin.rim_ramp_px)
    if guide is None:
        salient = diff_mask(cut, base) > 0.5
        frac = float(salient[band].mean())
        checks.append(
            _check(
                "rim_clear",
                frac,
                cfg.rim_salient_max,
                frac <= cfg.rim_salient_max,
                f"{100 * frac:.1f}% of the rim band painted over "
                f"(limit {100 * cfg.rim_salient_max:.0f}%)",
            )
        )
        touch = int((salient & (d >= 0) & (d < cfg.rim_touch_px)).sum())
        checks.append(
            _check(
                "rim_touch",
                touch,
                cfg.rim_touch_max,
                touch <= cfg.rim_touch_max,
                f"{touch} salient px within {cfg.rim_touch_px:g} px of the boundary "
                f"(limit {cfg.rim_touch_max})",
            )
        )
    else:
        if profile is None:
            raise ValueError("oriented cell gated without a profile")
        checks.extend(_oriented_checks(cut, base, final_rgb, guide, profile, pin, cfg))
    stats = tile_colour_stats(final)
    if guide is not None and ("feature_lum" in anchors or "feature_sat" in anchors):
        fstats = feature_colour_stats(final, base, guide.kind)
        for key, tol in (("lum", cfg.feature_lum_tol), ("sat", cfg.feature_sat_tol)):
            akey = f"feature_{key}"
            if akey in anchors and fstats["px"] > 0:
                delta = fstats[key] - anchors[akey]
                checks.append(
                    _check(
                        akey,
                        delta,
                        tol,
                        abs(delta) <= tol,
                        f"{guide.kind} {key} {fstats[key]} vs anchor {anchors[akey]} "
                        f"({delta:+.2f}, tol {tol})",
                        hard=False,
                    )
                )
    for key, tol in (("lum", cfg.lum_tol), ("hue", cfg.hue_tol)):
        if key in anchors:
            delta = stats[key] - anchors[key]
            checks.append(
                _check(
                    key,
                    delta,
                    tol,
                    abs(delta) <= tol,
                    f"{key} {stats[key]:.1f} vs anchor {anchors[key]:.1f} "
                    f"({delta:+.1f}, tol {tol})",
                    hard=False,
                )
            )
    return checks


def _oriented_checks(
    cut: np.ndarray,
    base: np.ndarray,
    final_rgb: np.ndarray,
    guide: Guide,
    profile: CrossProfile,
    pin: PinConfig,
    cfg: GateConfig,
) -> list[GateCheck]:
    checks: list[GateCheck] = []
    cut_mask = feature_mask(guide.kind, cut, base)
    if pin.paint_region_px > 0 and guide.kind == "road":
        # painting a track over relief: where it runs over rock of its own tan
        # the difference classifier sees only its dark cut edges, so bridge
        # the track between them before measuring Codex's crossing
        r = PAINT_OVER_CLOSE_PX
        cut_mask = min_filter(max_filter(cut_mask, r), r)
    final_mask = feature_mask(guide.kind, final_rgb, base)
    want_w = profile.feature_width
    hand_lo, hand_hi = pin.cross_ramp_px - 2.0, pin.cross_ramp_px + 4.0
    for edge in guide.edges:
        for stage, mask, lo, hi, cmax, wtol in (
            (
                "cut",
                cut_mask,
                hand_lo,
                hand_hi,
                cfg.cross_centre_max_cut,
                cfg.cross_width_rel_tol_cut * want_w,
            ),
            (
                "final",
                final_mask,
                0.5,
                2.5,
                cfg.cross_centre_max_final,
                cfg.cross_width_tol_final,
            ),
        ):
            name = f"cross_{stage}_e{edge}"
            c = measure_crossing(mask, edge, lo, hi)
            if c is None:
                checks.append(
                    _check(
                        name,
                        float("nan"),
                        cmax,
                        False,
                        f"no crossing at depth {lo}-{hi}",
                    )
                )
                continue
            ok = abs(c.centre) <= cmax and abs(c.width - want_w) <= wtol
            checks.append(
                _check(
                    name,
                    c.centre,
                    cmax,
                    ok,
                    f"centre {c.centre:+.2f} (limit {cmax}), width {c.width:.1f} vs "
                    f"{want_w:.1f} (tol {wtol:.1f}) at depth {lo:g}-{hi:g}",
                )
            )
    d = tile_distance()
    band = (d >= 0) & (d < cfg.leak_band_px)
    away = band & ~crossing_zone(guide, profile, cfg.leak_band_px)
    if pin.paint_region_px > 0:
        # painting over a finished tile: Codex's paint outside the guide region
        # never reaches the tile, so only a leak inside the region counts
        cut_mask = cut_mask * paint_region(guide, pin)
    for stage, mask in (("cut", cut_mask), ("final", final_mask)):
        n = int((mask[away] > 0.5).sum())
        checks.append(
            _check(
                f"leak_{stage}",
                n,
                cfg.leak_max_px,
                n <= cfg.leak_max_px,
                f"{n} feature px within {cfg.leak_band_px:g} px of a non-connected "
                f"rim (limit {cfg.leak_max_px})",
            )
        )
    return checks


def checks_to_json(checks: list[GateCheck]) -> list[dict[str, Any]]:
    """Serialisable list."""
    return [asdict(c) for c in checks]
