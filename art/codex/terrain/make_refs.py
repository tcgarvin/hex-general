"""Cut unit-free style-reference crops from docs/concept-art/concept1.png and
assemble them into one style board (refs/styleboard.png).

Crop boxes are (left, top, right, bottom) in concept1.png pixels; they are also
recorded in manifest.json. Every crop still shows the concept's thin pale hex
grid lines (they run through the whole map), but no units, badges, ZOC borders
or UI chrome.
"""

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
CONCEPT = HERE.parents[2] / "docs" / "concept-art" / "concept1.png"

CROPS: dict[str, tuple[int, int, int, int]] = {
    "hills": (8, 168, 300, 345),
    "east_forest": (1264, 300, 1448, 540),
    "river_bridge": (680, 690, 935, 815),
    "farmland": (935, 700, 1250, 815),
    "town": (940, 232, 1115, 370),
    "city": (20, 585, 470, 800),
    "ne_hills_forest": (1052, 62, 1235, 168),
    "center_forest": (600, 62, 800, 160),
}

BOARD_LAYOUT: list[list[str]] = [
    ["hills", "east_forest", "center_forest"],
    ["city", "town"],
    ["river_bridge", "farmland", "ne_hills_forest"],
]
GAP = 12
BOARD_SCALE = 1.5


def main() -> None:
    concept = Image.open(CONCEPT).convert("RGB")
    out = HERE / "refs"
    out.mkdir(exist_ok=True)
    crops = {name: concept.crop(box) for name, box in CROPS.items()}
    for name, im in crops.items():
        im.save(out / f"crop_{name}.png")
    rows = [[crops[n] for n in row] for row in BOARD_LAYOUT]
    width = max(sum(im.width for im in row) + GAP * (len(row) + 1) for row in rows)
    height = sum(max(im.height for im in row) for row in rows) + GAP * (len(rows) + 1)
    board = Image.new("RGB", (width, height), (40, 40, 38))
    y = GAP
    for row in rows:
        x = GAP
        for im in row:
            board.paste(im, (x, y))
            x += im.width + GAP
        y += max(im.height for im in row) + GAP
    size = (round(width * BOARD_SCALE), round(height * BOARD_SCALE))
    board.resize(size, Image.LANCZOS).save(out / "styleboard.png")
    print(f"styleboard {size}")


if __name__ == "__main__":
    main()
