#!/usr/bin/env python3
"""Emit frontend-ready UI chrome sprites from the ratified kit cuts.

The bridge between the foundry cuts (art/foundry/ui/cut/, produced by
ui-kit-cut.py) and what the Pixi chrome actually loads
(frontend/src/board/panels/chrome/). Each emit downscales a cut to roughly 2x
its on-screen size (crisp on hidpi, cheap on VRAM) and renames it for the
consumer. Deterministic: re-run after re-cutting; output is fully derived.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
CUT_DIR = REPO / "art" / "foundry" / "ui" / "cut"
DEFAULT_OUT_DIR = REPO / "frontend" / "src" / "board" / "panels" / "chrome"


class Emit(NamedTuple):
    source: str  # filename under CUT_DIR (without .png)
    scale: float


# On-screen targets (1x): order buttons ~64px, the portrait well ~130px,
# nameplate height ~24px, commit button ~110px wide. Scales below land each
# sprite near 2x those targets.
EMITS: dict[str, Emit] = {
    "btn-up": Emit("btnblank-up", 0.40),
    "btn-hover": Emit("btnblank-hover", 0.40),
    "btn-pressed": Emit("btnblank-pressed", 0.40),
    "btn-disabled": Emit("btnblank-disabled", 0.40),
    "btn-active": Emit("btnblank-active", 0.40),
    "commit-up": Emit("commit-up", 0.40),
    "commit-pressed": Emit("commit-pressed", 0.40),
    "commit-disabled": Emit("commit-disabled", 0.40),
    "iconbtn-up": Emit("iconbtn2-up", 0.40),
    "iconbtn-pressed": Emit("iconbtn2-pressed", 0.40),
    # Emitted smaller than the buttons: both render as NineSliceSprites at
    # small on-screen sizes (well ~124px square, plate ~28px tall), and a
    # NineSlice border renders at the texture's own pixel thickness — the
    # emit scale is what sets the on-screen border weight.
    "well": Emit("inset-well", 0.25),
    "plate": Emit("nameplate", 0.25),
    "canvas": Emit("tex-canvas", 1.00),
    "parchment": Emit("tex-parchment", 1.00),
    "wood": Emit("tex-wood", 1.00),
    "frame": Emit("panel-frame", 0.50),
    "tab-active": Emit("tab-active", 0.50),
    "tab-inactive": Emit("tab-inactive", 0.50),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="output directory (default frontend/src/board/panels/chrome)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for name, emit in EMITS.items():
        src = CUT_DIR / f"{emit.source}.png"
        img = Image.open(src).convert("RGBA")
        if emit.scale != 1.0:
            size = (
                max(1, round(img.width * emit.scale)),
                max(1, round(img.height * emit.scale)),
            )
            img = img.resize(size, Image.LANCZOS)
        out = args.out_dir / f"{name}.png"
        img.save(out, optimize=True)
        print(f"{name:<18}{emit.source:<22}x{emit.scale:<6}{img.size}")

    print(f"emitted {len(EMITS)} sprites -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
