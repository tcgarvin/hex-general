"""Make the level-2 derrick reference image for the sites4 oil L3 retry (s4-l3d).

Places the given level-2 site tiles side by side at the paint-in sheet's cell scale
(340 px hex width = 128 px tile x 2.65625), on a dark neutral background, so Codex
sees a level-2 derrick at the exact pixel size it should paint at level 3.

usage: python3 art/codex/terrain/sites4_oilfac_l2ref.py <out.png> <tile.png> [...]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

HEX_W = 340
GAP = 40
BG = (40, 40, 38)


def main() -> int:
    out, *tiles = sys.argv[1:]
    if not tiles:
        raise SystemExit(__doc__)
    w = len(tiles) * HEX_W + (len(tiles) + 1) * GAP
    im = Image.new("RGBA", (w, HEX_W + 2 * GAP), BG + (255,))
    for i, p in enumerate(tiles):
        t = Image.open(p).convert("RGBA").resize((HEX_W, HEX_W), Image.Resampling.LANCZOS)
        im.alpha_composite(t, (GAP + i * (HEX_W + GAP), GAP))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
