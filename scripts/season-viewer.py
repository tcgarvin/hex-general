#!/usr/bin/env python3
"""Build the season viewer: one self-contained HTML page that plays a recorded
world season back on a map.

Usage:
    python3 scripts/season-viewer.py .dev/season/season.json \
        --out .dev/season/season.html [--title "Season Viewer"] [--fragment] \
        [--encoding auto|gzip|json]

The input is a record written by ``uv run python -m app.world.season`` (see
``api/app/world/season.py``). The page's CSS, markup and script live in
``scripts/season_viewer/``; this tool inlines them with the record, so the page
makes no outside request of any kind and opens straight from disk.

``--encoding gzip`` embeds the record gzip-compressed and base64-encoded, which
the page unpacks with the browser's ``DecompressionStream``; ``json`` embeds it as
plain JSON; ``auto`` (the default) uses plain JSON for small records only.

By default the output is a full standalone HTML document. ``--fragment`` emits the
claude.ai Artifact form instead: it starts with ``<title>``, the viewport-fit
meta and ``<style>``, with no doctype, ``<html>``, ``<head>`` or ``<body>`` (the
Artifact tool wraps it in its own skeleton).
"""

from __future__ import annotations

import argparse
import base64
import gzip
import html
import json
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "season_viewer"
SCHEMA_VERSION = 1
MAX_BYTES = 12_000_000
PLAIN_LIMIT = 400_000  # ``auto`` embeds plain JSON below this many bytes
_REQUIRED = (
    "v",
    "slots",
    "bots",
    "seed",
    "turns",
    "players",
    "bases",
    "map",
    "frames",
    "eliminated",
)


class ViewerError(Exception):
    """The record cannot be turned into a viewer page."""


def load_record(path: Path) -> dict[str, object]:
    """Read and sanity-check a season record (this tool's input boundary)."""
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as err:
        raise ViewerError(f"cannot read season record {path}: {err}") from err
    if not isinstance(record, dict):
        raise ViewerError(f"{path} is not a season record (top level is not an object)")
    missing = [key for key in _REQUIRED if key not in record]
    if missing:
        raise ViewerError(f"{path} is missing {', '.join(missing)}")
    if record["v"] != SCHEMA_VERSION:
        raise ViewerError(
            f"{path} is record version {record['v']}, this viewer reads {SCHEMA_VERSION}"
        )
    frames = record["frames"]
    if not isinstance(frames, list) or not frames:
        raise ViewerError(f"{path} has no frames")
    return record


def embed_data(record: dict[str, object], encoding: str) -> tuple[str, str]:
    """The record as ``(encoding, payload)`` for the page's data script tag."""
    text = json.dumps(record, separators=(",", ":"))
    if encoding == "auto":
        encoding = "json" if len(text) < PLAIN_LIMIT else "gzip"
    if encoding == "json":
        # Inside <script>, only "</" could end the element early.
        return "json", text.replace("</", "<\\/")
    if encoding == "gzip":
        packed = gzip.compress(text.encode(), compresslevel=9, mtime=0)
        return "gzip", base64.b64encode(packed).decode("ascii")
    raise ViewerError(f"unknown encoding {encoding!r}")


def build_page(
    record: dict[str, object], title: str, fragment: bool, encoding: str = "auto"
) -> str:
    """The whole viewer page for ``record``."""
    css = (ASSETS / "viewer.css").read_text()
    body = (ASSETS / "body.html").read_text().replace("{{TITLE}}", html.escape(title))
    script = (ASSETS / "viewer.js").read_text()
    kind, payload = embed_data(record, encoding)
    data = (
        f'<script id="season-data" type="application/json" data-encoding="{kind}">'
        f"{payload}</script>"
    )
    content = f"{body}\n{data}\n<script>\n{script}</script>\n"
    head = (
        f"<title>{html.escape(title)}</title>\n"
        '<meta name="claude:viewport-fit-sides" content="cover">\n'
        f"<style>\n{css}</style>\n"
    )
    if fragment:
        return head + content
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, '
        'viewport-fit=cover">\n'
        f"{head}</head>\n<body>\n{content}</body>\n</html>\n"
    )


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("season", type=Path, help="season record JSON")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="Season Viewer")
    parser.add_argument(
        "--fragment",
        action="store_true",
        help="emit the claude.ai Artifact fragment instead of a full document",
    )
    parser.add_argument("--encoding", choices=("auto", "gzip", "json"), default="auto")
    args = parser.parse_args()
    try:
        record = load_record(args.season)
        page = build_page(record, args.title, args.fragment, args.encoding)
    except ViewerError as err:
        print(f"season-viewer: {err}", file=sys.stderr)
        return 1
    size = len(page.encode())
    if size > MAX_BYTES:
        print(
            f"season-viewer: page is {size / 1e6:.1f} MB, over the "
            f"{MAX_BYTES / 1e6:.0f} MB limit",
            file=sys.stderr,
        )
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    print(f"wrote {args.out} ({size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
