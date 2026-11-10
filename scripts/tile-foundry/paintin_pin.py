"""Pinning a Codex-painted cell back to its deterministic ground (`paintin.py pin`).

`pin_tile` builds the final 128 px RGBA tile from three inputs: the cut cell
(Codex's painting, registered and resampled), the base tile it was painted on,
and the cell's guide (oriented cells only). It blends between a deterministic
**target** and the painted **interior** with a per-pixel weight `w`:

    out = target * (1 - w) + interior * w

- `target` is the base tile, plus, for an oriented cell, the canonical
  cross-section **profile** of the feature extruded inward from every connected
  edge (`CrossProfile`). The profile is one symmetric 1-D colour + alpha
  function of the canonical edge tangent (`paintin_geom.edge_tangent`), so two
  neighbours — mirrored or not, across a vertical or diagonal edge — evaluate
  the same colours at the same board point.
- `w` is 0 within `rim_exact_px` of the hex boundary (so the rim equals the
  base exactly), ramps to 1 over `rim_ramp_px`; inside a crossing it stays 0 to
  `cross_exact_px` and ramps to 1 by `cross_ramp_px`, laterally feathered by
  the profile alpha.
- `interior` is the cut cell, or with **ground restore** a difference-matte
  composite that keeps the base's own ground wherever Codex painted no feature.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np

from paintin_geom import (
    TILE_PX,
    Guide,
    apothem,
    edge_frame,
    edge_normal,
    edge_tangent,
    gaussian_blur,
    max_filter,
    min_filter,
    pixel_grid,
    render_guide_mask,
    render_marker_mask,
    sample_bilinear,
    smoothstep,
    tile_distance,
)

# ------------------------------------------------------------ classifiers ----

WATER_SAT = (0.24, 0.36)  # saturation ramp: water below, ground above
WATER_VAL_MIN = (0.16, 0.24)  # very dark bank/shadow pixels are not water
# Water is cool (concept slate 163 deg); banks, khaki lips, grass and bridge
# stone are warm (40-65 deg) and some of them are as desaturated as water, so
# hue decides: cool hues ramp in above WATER_HUE_LO and out past WATER_HUE_HI.
# Near-neutral pixels (saturation below WATER_NEUTRAL_SAT) have no reliable
# hue and count as water, as before.
WATER_HUE_LO = (68.0, 80.0)
WATER_HUE_HI = (250.0, 265.0)
WATER_NEUTRAL_SAT = (0.04, 0.08)
DIFF_LEVELS = (24.0, 40.0)  # smoothed max-channel |painted - base| ramp: "painted"
DIFF_SIGMA = 0.75

FEATURE_CLASSIFIER: dict[str, str] = {"river": "water", "road": "diff"}


def hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hue (degrees), saturation and value (0..1) of a float RGB (0..255) array."""
    c = rgb / 255.0
    mx = c.max(axis=-1)
    mn = c.min(axis=-1)
    delta = mx - mn
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-9), 0.0)
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    safe = np.maximum(delta, 1e-9)
    hue = np.where(
        mx == r,
        ((g - b) / safe) % 6.0,
        np.where(mx == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0),
    )
    hue = np.where(delta > 0, hue * 60.0, 0.0)
    return hue, sat, mx


def water_mask(rgb: np.ndarray) -> np.ndarray:
    """Soft water classifier: desaturated, cool (or neutral), not near-black."""
    hue, sat, val = hsv(rgb)
    cool = smoothstep(WATER_HUE_LO[0], WATER_HUE_LO[1], hue) * (
        1.0 - smoothstep(WATER_HUE_HI[0], WATER_HUE_HI[1], hue)
    )
    neutral = 1.0 - smoothstep(WATER_NEUTRAL_SAT[0], WATER_NEUTRAL_SAT[1], sat)
    return (
        (1.0 - smoothstep(WATER_SAT[0], WATER_SAT[1], sat))
        * smoothstep(WATER_VAL_MIN[0], WATER_VAL_MIN[1], val)
        * np.maximum(cool, neutral)
    )


def colour_distance(rgb: np.ndarray, base: np.ndarray, sigma: float) -> np.ndarray:
    """Max-channel |blur(rgb) - blur(base)|.

    Max-channel rather than mean: a terracotta roof on olive grass differs
    mostly in one channel (green), which a mean dilutes below threshold.
    """
    return np.abs(gaussian_blur(rgb, sigma) - gaussian_blur(base, sigma)).max(axis=-1)


def diff_mask(rgb: np.ndarray, base: np.ndarray) -> np.ndarray:
    """Soft "painted feature" classifier: smoothed colour distance from the base."""
    return smoothstep(
        DIFF_LEVELS[0], DIFF_LEVELS[1], colour_distance(rgb, base, DIFF_SIGMA)
    )


def feature_mask(kind: str, rgb: np.ndarray, base: np.ndarray) -> np.ndarray:
    """The feature classifier for a guide kind (see FEATURE_CLASSIFIER)."""
    if FEATURE_CLASSIFIER[kind] == "water":
        return water_mask(rgb)
    return diff_mask(rgb, base)


# ------------------------------------------------------- crossing measure ----


@dataclass(frozen=True)
class Crossing:
    """A feature's crossing of one edge: centre offset along the canonical
    tangent and width (tile px), medians over the sampled depth rows."""

    edge: int
    centre: float
    width: float
    rows_found: int
    rows_total: int


def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    idx = np.flatnonzero(row)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate([[idx[0]], idx[breaks + 1]])
    ends = np.concatenate([idx[breaks], [idx[-1]]])
    return list(zip(starts.tolist(), ends.tolist()))


def edge_samples(
    edge: int, depths: np.ndarray, ts: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Tile-space (x, y) of a (depth, t) grid in `edge`'s frame."""
    nx, ny = edge_normal(edge)
    tx, ty = edge_tangent(edge)
    a = apothem()
    r = a - depths[:, None]
    return (64.0 + r * nx + ts[None, :] * tx, 64.0 + r * ny + ts[None, :] * ty)


def measure_crossing(
    mask: np.ndarray,
    edge: int,
    depth_lo: float,
    depth_hi: float,
    search_half: float = 30.0,
    step: float = 0.25,
) -> Crossing | None:
    """Measure where a soft feature mask crosses `edge` (None: no crossing found).

    Samples rows parallel to the edge at depths `depth_lo..depth_hi` (1 px
    apart); in each row takes the run of mask > 0.5 nearest the midpoint. A
    crossing needs a run in at least half the rows; centre and width are the
    medians over the rows that have one.
    """
    depths = np.arange(depth_lo, depth_hi + 1e-9, 1.0)
    ts = np.arange(-search_half, search_half + 1e-9, step)
    xs, ys = edge_samples(edge, depths, ts)
    rows = sample_bilinear(mask, xs, ys) > 0.5
    centres = []
    widths = []
    for row in rows:
        runs = _runs(row)
        if not runs:
            continue
        s, e = min(runs, key=lambda r: abs(ts[r[0]] + ts[r[1]]))
        centres.append((ts[s] + ts[e]) / 2.0)
        widths.append(ts[e] - ts[s] + step)
    if len(centres) < max(1, len(depths) / 2):
        return None
    return Crossing(
        edge=edge,
        centre=float(np.median(centres)),
        width=float(np.median(widths)),
        rows_found=len(centres),
        rows_total=len(depths),
    )


# --------------------------------------------------------------- profile ----

PROFILE_STEP = 0.25


@dataclass(frozen=True)
class CrossProfile:
    """Canonical cross-section of an oriented feature where it crosses an edge.

    `rgb`/`alpha` are sampled at `t` (symmetric about 0, step PROFILE_STEP).
    `feature_width` is the classifier width at the source crossing; alpha is 1
    out to `core_half` (feature + bank margin) and feathers to 0 over `feather`.
    """

    kind: str
    t: np.ndarray
    rgb: np.ndarray
    alpha: np.ndarray
    feature_width: float
    core_half: float
    feather: float
    source: dict[str, Any]

    def sample(self, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(rgb, alpha) at tangent coordinates `t`; alpha is 0 beyond the span."""
        rgb = np.stack(
            [np.interp(t, self.t, self.rgb[:, c]) for c in range(3)], axis=-1
        )
        alpha = np.interp(t, self.t, self.alpha, left=0.0, right=0.0)
        return rgb, alpha

    def to_json(self) -> dict[str, Any]:
        """Serialisable form (lists of rounded floats)."""
        return {
            "kind": self.kind,
            "step": PROFILE_STEP,
            "t": [round(float(v), 4) for v in self.t],
            "rgb": [[round(float(c), 3) for c in px] for px in self.rgb],
            "alpha": [round(float(v), 5) for v in self.alpha],
            "feature_width": self.feature_width,
            "core_half": self.core_half,
            "feather": self.feather,
            "source": self.source,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> CrossProfile:
        """Inverse of `to_json`."""
        return cls(
            kind=raw["kind"],
            t=np.asarray(raw["t"], dtype=np.float64),
            rgb=np.asarray(raw["rgb"], dtype=np.float64),
            alpha=np.asarray(raw["alpha"], dtype=np.float64),
            feature_width=float(raw["feature_width"]),
            core_half=float(raw["core_half"]),
            feather=float(raw["feather"]),
            source=raw["source"],
        )

    def save(self, path: Path) -> None:
        """Write the profile JSON."""
        path.write_text(json.dumps(self.to_json(), indent=1))

    @classmethod
    def load(cls, path: Path) -> CrossProfile:
        """Read a profile JSON written by `save`."""
        return cls.from_json(json.loads(path.read_text()))


class ProfileError(RuntimeError):
    """A cross-section profile could not be extracted."""


def extract_profile(
    cut: np.ndarray,
    base: np.ndarray,
    kind: str,
    edges: tuple[int, ...],
    source: dict[str, Any],
    depth_lo: float = 2.0,
    depth_hi: float = 9.0,
    bank_px: float = 3.0,
    feather_px: float = 4.0,
) -> CrossProfile:
    """Extract a symmetric cross-section profile from a painted cut tile.

    For each listed edge and each depth row in `depth_lo..depth_hi`, the row is
    re-centred on its measured crossing centre and sampled along the canonical
    tangent; rows and edges are averaged, then the result is mirrored about
    t = 0 and averaged with itself so that a left-right mirrored tile and its
    unmirrored neighbour agree (see module docstring).
    """
    mask = feature_mask(kind, cut, base)
    core_guess = 0.0
    rows_rgb = []
    widths = []
    for edge in edges:
        crossing = measure_crossing(mask, edge, depth_lo, depth_hi)
        if crossing is None:
            raise ProfileError(f"{source}: no {kind} crossing found at edge {edge}")
        widths.append(crossing.width)
        core_guess = max(core_guess, crossing.width / 2.0 + bank_px + feather_px + 2.0)
    half_span = core_guess
    ts = np.arange(-half_span, half_span + 1e-9, PROFILE_STEP)
    for edge in edges:
        for depth in np.arange(depth_lo, depth_hi + 1e-9, 1.0):
            row = measure_crossing(mask, edge, depth, depth)
            if row is None:
                raise ProfileError(
                    f"{source}: {kind} crossing at edge {edge} has a gap at depth {depth}"
                )
            xs, ys = edge_samples(edge, np.array([depth]), ts + row.centre)
            rows_rgb.append(sample_bilinear(cut, xs, ys)[0])
    rgb = np.mean(rows_rgb, axis=0)
    asym = float(np.abs(rgb - rgb[::-1]).mean())
    rgb = (rgb + rgb[::-1]) / 2.0
    width = float(np.mean(widths))
    core_half = width / 2.0 + bank_px
    alpha = 1.0 - smoothstep(core_half, core_half + feather_px, np.abs(ts))
    return CrossProfile(
        kind=kind,
        t=ts,
        rgb=rgb,
        alpha=alpha,
        feature_width=width,
        core_half=core_half,
        feather=feather_px,
        source={
            **source,
            "edges": list(edges),
            "edge_widths": widths,
            "depths": [depth_lo, depth_hi],
            "bank_px": bank_px,
            "asymmetry_mean_abs_rgb": round(asym, 2),
        },
    )


# ------------------------------------------------------------------- pin ----


@dataclass(frozen=True)
class PinConfig:
    """Pin parameters (tile px). See the module docstring."""

    rim_exact_px: float = 1.5
    rim_ramp_px: float = 6.0
    cross_exact_px: float = 3.0
    cross_ramp_px: float = 12.0
    restore: bool = False
    restore_sigma: float = 0.75
    restore_lo: float = 14.0
    restore_hi: float = 26.0
    restore_close_px: int = 4
    restore_grow_px: int = 2
    restore_feather_px: float = 1.2
    restore_tone: bool = True
    restore_tone_sigma: float = 8.0
    restore_tone_span: float = 20.0
    # per-sheet feature colour normalisation (see `normalise_feature`): strength
    # 0 is off; otherwise feature_rgb is the [r, g, b] target the feature's mean
    # colour is pulled toward
    feature_strength: float = 0.0
    feature_rgb: tuple[float, ...] = ()
    hold_feather_px: float = 3.0  # lateral feather of a held base crossing
    # rim-pin mode: remove the painting's slow ground-tone drift from the base
    # (Gaussian sigma in tile px; 0 is off), see `tone_match`
    tone_sigma: float = 0.0
    tone_feature_grow_px: int = 2
    # painting over a finished tile (bridges): take Codex's paint only within
    # this many px of the guide (ribbon + marker), the base everywhere else;
    # 0 is off
    paint_region_px: float = 0.0
    paint_region_feather_px: float = 2.0

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> PinConfig:
        """Build from a job's `pin` object; unknown keys raise."""
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(
                f"unknown pin settings {sorted(unknown)}; known: {sorted(known)}"
            )
        cfg = dict(raw)
        if "feature_rgb" in cfg:
            cfg["feature_rgb"] = tuple(float(v) for v in cfg["feature_rgb"])
        out = cls(**cfg)
        if not 0.0 <= out.feature_strength <= 1.0:
            raise ValueError(f"feature_strength must be 0..1, got {out.feature_strength}")
        if out.feature_strength > 0 and len(out.feature_rgb) != 3:
            raise ValueError("feature_strength > 0 needs feature_rgb = [r, g, b]")
        return out

    def to_json(self) -> dict[str, Any]:
        """Serialisable form."""
        return asdict(self)


@dataclass(frozen=True)
class PinResult:
    """A pinned tile and its intermediates (float arrays, 128 x 128)."""

    rgba: np.ndarray  # uint8 (128, 128, 4)
    target: np.ndarray  # deterministic target RGB
    weight: np.ndarray  # painted weight w
    matte: np.ndarray  # ground-restore feature matte (all ones when restore is off)
    interior: np.ndarray  # RGB blended in with weight w


def pin_target(
    base: np.ndarray, guide: Guide | None, profile: CrossProfile | None, cfg: PinConfig
) -> tuple[np.ndarray, np.ndarray]:
    """(target RGB, painted weight w) for a cell; see the module docstring.

    `profile` is required (and only used) when `guide` is not None.
    """
    d = tile_distance()
    weight = smoothstep(cfg.rim_exact_px, cfg.rim_exact_px + cfg.rim_ramp_px, d)
    target = base.copy()
    if guide is None:
        return target, weight
    if profile is None:
        raise ValueError("an oriented cell needs a cross-section profile")
    if profile.kind != guide.kind:
        raise ValueError(f"profile kind {profile.kind!r} != guide kind {guide.kind!r}")
    xs, ys = pixel_grid(TILE_PX)
    for edge in guide.edges:
        depth, t = edge_frame(edge, xs, ys)
        rgb, alpha = profile.sample(t)
        zone = depth <= cfg.cross_ramp_px
        a = np.where(zone, alpha, 0.0)
        target = target * (1 - a[..., None]) + rgb * a[..., None]
        hold = 1.0 - smoothstep(cfg.cross_exact_px, cfg.cross_ramp_px, depth)
        weight = np.minimum(weight, 1.0 - a * hold)
    for edge in guide.hold_edges:
        # painting over a finished tile: keep the base's own crossing here
        depth, t = edge_frame(edge, xs, ys)
        lateral = 1.0 - smoothstep(
            guide.hold_half, guide.hold_half + cfg.hold_feather_px, np.abs(t)
        )
        hold = 1.0 - smoothstep(cfg.cross_exact_px, cfg.cross_ramp_px, depth)
        weight = np.minimum(weight, 1.0 - lateral * hold)
    return target, weight


class NormaliseError(RuntimeError):
    """A feature could not be found to colour-normalise."""


NORMALISE_MIN_PX = 40  # fewer core feature pixels than this cannot set a mean


def normalise_feature(
    cut: np.ndarray, base: np.ndarray, guide: Guide | None, cfg: PinConfig
) -> tuple[np.ndarray, dict[str, Any]]:
    """Pull a painted feature's mean colour toward `cfg.feature_rgb`.

    Codex keys each sheet a little differently (one sheet's water bluer or
    lighter than the next). With `feature_strength` > 0, the feature's mean
    colour over its classifier core (mask > 0.5, at least 3 px inside the
    hex) moves `feature_strength` of the way to the target; the same offset is
    added to every pixel in proportion to its soft mask, so texture, banks and
    ground are untouched. Returns the cut and a record of the shift.
    """
    if guide is None or cfg.feature_strength == 0.0:
        return cut, {}
    mask = feature_mask(guide.kind, cut, base)
    if cfg.paint_region_px > 0:
        # painting over a finished tile: only the guide's region is the feature
        # (Codex's repaint elsewhere is discarded by the pin anyway)
        mask = mask * paint_region(guide, cfg)
    core = (mask > 0.5) & (tile_distance() > 3.0)
    if int(core.sum()) < NORMALISE_MIN_PX:
        raise NormaliseError(
            f"only {int(core.sum())} {guide.kind} pixels to normalise "
            f"(need {NORMALISE_MIN_PX})"
        )
    mean = cut[core].mean(axis=0)
    offset = cfg.feature_strength * (np.asarray(cfg.feature_rgb) - mean)
    out = cut + offset[None, None, :] * mask[..., None]
    return out, {
        "mean_before": [round(float(v), 1) for v in mean],
        "offset": [round(float(v), 1) for v in offset],
    }


def ground_restore(
    cut: np.ndarray, base: np.ndarray, cfg: PinConfig
) -> tuple[np.ndarray, np.ndarray]:
    """Difference-matte composite: base ground where Codex painted no feature.

    The matte is the smoothed per-pixel colour distance between the painting
    and the base, ramped between `restore_lo` and `restore_hi`, closed with a
    `restore_close_px` disc (so olive-ish pixels *inside* a feature — gardens,
    trees in a town — do not punch base-grass holes through it), grown by
    `restore_grow_px` (to keep soft contact shadows) and feathered. With
    `restore_tone`, the painting's low-frequency ground offset from the base
    (measured where the matte is clear) is removed from the kept pixels that
    still look like ground (distance below `restore_hi`, fading out over
    `restore_tone_span`), so the seam between kept and restored ground has no
    tone step while the features keep their own colours.
    """
    diff = colour_distance(cut, base, cfg.restore_sigma)
    matte = smoothstep(cfg.restore_lo, cfg.restore_hi, diff)
    if cfg.restore_close_px > 0:
        matte = min_filter(
            max_filter(matte, cfg.restore_close_px), cfg.restore_close_px
        )
    matte = max_filter(matte, cfg.restore_grow_px)
    matte = np.clip(gaussian_blur(matte, cfg.restore_feather_px), 0.0, 1.0)
    kept = cut
    if cfg.restore_tone:
        clear = 1.0 - matte
        num = gaussian_blur((base - cut) * clear[..., None], cfg.restore_tone_sigma)
        den = gaussian_blur(clear, cfg.restore_tone_sigma)[..., None]
        global_offset = ((base - cut) * clear[..., None]).sum(axis=(0, 1)) / max(
            clear.sum(), 1.0
        )
        local_ok = den > 0.05
        offset = np.where(local_ok, num / np.maximum(den, 1e-9), global_offset)
        groundlike = 1.0 - smoothstep(
            cfg.restore_hi, cfg.restore_hi + cfg.restore_tone_span, diff
        )
        kept = cut + offset * groundlike[..., None]
    out = base * (1.0 - matte[..., None]) + kept * matte[..., None]
    return out, matte


def tone_match(
    cut: np.ndarray, base: np.ndarray, guide: Guide | None, cfg: PinConfig
) -> tuple[np.ndarray, np.ndarray]:
    """Remove Codex's low-frequency ground tone drift relative to the base.

    Codex re-renders the ground with slow tone changes of its own (often a
    slightly darker band just inside the hex edge), which the exact rim pin
    turns into a faint hex outline. The local mean of (base - cut) over
    ground pixels (not the feature, grown by `tone_feature_grow_px`), at
    Gaussian scale `tone_sigma`, is added back to every ground-like pixel,
    fading out over the feature. Fine texture, banks and the feature itself
    keep Codex's painting. Returns (corrected cut, applied offset magnitude).
    """
    if guide is None:
        feature = diff_mask(cut, base)
    else:
        feature = np.maximum(feature_mask(guide.kind, cut, base), diff_mask(cut, base))
    feature = max_filter(feature, cfg.tone_feature_grow_px)
    ground = 1.0 - feature
    num = gaussian_blur((base - cut) * ground[..., None], cfg.tone_sigma)
    den = gaussian_blur(ground, cfg.tone_sigma)[..., None]
    global_offset = ((base - cut) * ground[..., None]).sum(axis=(0, 1)) / max(
        float(ground.sum()), 1.0
    )
    offset = np.where(den > 0.05, num / np.maximum(den, 1e-9), global_offset)
    out = cut + offset * ground[..., None]
    return out, np.abs(offset).mean(axis=-1)


def paint_region(guide: Guide, cfg: PinConfig) -> np.ndarray:
    """Where Codex's paint is taken when painting over a finished tile: the
    guide's ribbon and marker, grown by `paint_region_px` (room for frayed
    track edges, the bridge's south face and its shadow) and feathered."""
    region = render_guide_mask(guide, TILE_PX, float(TILE_PX))
    if guide.marker is not None:
        region = np.maximum(region, render_marker_mask(guide.marker, TILE_PX, float(TILE_PX)))
    region = max_filter(region, int(round(cfg.paint_region_px)))
    return np.clip(gaussian_blur(region, cfg.paint_region_feather_px), 0.0, 1.0)


def pin_tile(
    cut: np.ndarray,
    base: np.ndarray,
    alpha: np.ndarray,
    guide: Guide | None,
    profile: CrossProfile | None,
    cfg: PinConfig,
) -> PinResult:
    """Pin a cut cell to its base; returns the final RGBA tile and intermediates."""
    if cut.shape != (TILE_PX, TILE_PX, 3) or base.shape != (TILE_PX, TILE_PX, 3):
        raise ValueError(f"cut {cut.shape} and base {base.shape} must be 128x128x3")
    target, weight = pin_target(base, guide, profile, cfg)
    if cfg.paint_region_px > 0 and guide is not None:
        weight = weight * paint_region(guide, cfg)
    if cfg.restore:
        interior, matte = ground_restore(cut, base, cfg)
    elif cfg.tone_sigma > 0:
        interior, _ = tone_match(cut, base, guide, cfg)
        matte = np.ones(cut.shape[:2])
    else:
        interior, matte = cut, np.ones(cut.shape[:2])
    rgb = target * (1.0 - weight[..., None]) + interior * weight[..., None]
    rgba = np.empty((TILE_PX, TILE_PX, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    rgba[..., 3] = alpha
    return PinResult(
        rgba=rgba, target=target, weight=weight, matte=matte, interior=interior
    )
