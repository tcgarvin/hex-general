"""The review page for a batch of staff-map renders.

Built with ``scripts/review-page.py`` (self-contained HTML, data-URI images),
then two passes it does not do: a notes paragraph under each section heading
(as ``art/codex/ui/add_notes.py`` does) and a re-encode of every embedded image
as JPEG, stepping the quality down until the page fits a byte budget.
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1]
STYLE_ORDER = ("current", "staff-map", "staff-map-night", "painted-far", "hybrid")
SECTIONS = (
    ("world", "Whole world"),
    ("region", "Region, 10 px per hex"),
    ("near", "Near, 22 px per hex"),
)
STRIPS = "Transition strips"
_DATA_URI = re.compile(r'src="data:image/(?:png|jpeg);base64,([A-Za-z0-9+/=]+)"')


def build(render_dir: Path, out: Path, notes_path: Path, max_bytes: int) -> int:
    """Write the page; return its size in bytes. Raises if it cannot fit."""
    notes = json.loads(notes_path.read_text())
    args = [
        sys.executable,
        str(SCRIPTS / "review-page.py"),
        "--title",
        notes["title"],
        "--out",
        str(out),
        "--intro",
        notes["intro"],
    ]
    for view, name in SECTIONS:
        args += ["--section", name]
        args += [f"{s}={render_dir / f'{s}-{view}.png'}" for s in STYLE_ORDER]
    args += ["--section", STRIPS, f"board={render_dir / 'board-strip.png'}"]
    args += [f"{s}={render_dir / f'{s}-strip.png'}" for s in STYLE_ORDER]
    subprocess.run(args, check=True, capture_output=True)
    doc = out.read_text(encoding="utf-8")
    for section, text in notes["sections"].items():
        head = f"<h2>{html.escape(section, quote=True)}</h2>"
        if doc.count(head) != 1:
            raise ValueError(f"section heading not found exactly once: {section!r}")
        paragraphs = "".join(f'<p class="intro">{html.escape(p)}</p>' for p in text)
        doc = doc.replace(head, f"{head}\n{paragraphs}")
    for quality in (88, 84, 80, 76, 72):
        shrunk = _DATA_URI.sub(lambda m: _jpeg(m.group(1), quality), doc)
        size = len(shrunk.encode("utf-8"))
        if size <= max_bytes:
            out.write_text(shrunk, encoding="utf-8")
            return size
    raise ValueError(f"page is {size} bytes at JPEG quality 72, over {max_bytes}")


def _jpeg(b64: str, quality: int) -> str:
    image = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    buf = io.BytesIO()
    # Full-resolution chroma: thin coloured lines (borders, rivers) smear at 4:2:0.
    image.save(buf, format="JPEG", quality=quality, subsampling=0, optimize=True)
    return (
        'src="data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode() + '"'
    )
