"""Build the P5 in-context paint-in input: a 3x3 sheet of separated flat-top
hexagons filled with a P3 ground swatch, on flat magenta, at Codex's square
output size. Writes refs/paintin_sheet.png and refs/paintin_sheet.json (hex
centres + corners) for measure_paintin.py.

Usage: python3 make_paintin_sheet.py raw/P3b-ground-mottled.png
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts" / "tile-foundry"))
import hexgeom  # noqa: E402

CANVAS = 1254
CELL = CANVAS / 3
HEX_WIDTH = 340
MAGENTA = (255, 0, 255)


def main() -> None:
    ground = Image.open(sys.argv[1]).convert("RGB").resize((CANVAS, CANVAS), Image.LANCZOS)
    sheet = Image.new("RGB", (CANVAS, CANVAS), MAGENTA)
    mask = Image.new("L", (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(mask)
    hexes = []
    for row in range(3):
        for col in range(3):
            cx, cy = (col + 0.5) * CELL, (row + 0.5) * CELL
            corners = hexgeom.corner_points(cx, cy, HEX_WIDTH / 2)
            draw.polygon(corners, fill=255)
            hexes.append({"cx": cx, "cy": cy, "corners": [list(p) for p in corners]})
    # Ground is pasted from the same position, so each hex shows a different patch.
    sheet.paste(ground, (0, 0), mask)
    out = HERE / "refs"
    sheet.save(out / "paintin_sheet.png")
    meta = {"canvas": CANVAS, "hex_width": HEX_WIDTH, "ground": sys.argv[1], "hexes": hexes}
    (out / "paintin_sheet.json").write_text(json.dumps(meta, indent=1))
    print("ok")


if __name__ == "__main__":
    main()
