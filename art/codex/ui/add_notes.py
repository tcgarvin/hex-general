#!/usr/bin/env python3
"""Insert a notes paragraph under named sections of a scripts/review-page.py page.

review-page.py has a page intro but no per-section text; this adds a
<p class="intro"> right after each matching <h2>, in place.

Usage: add_notes.py page.html "Section name" "note text" ["Section" "text" ...]
"""

from __future__ import annotations

import html
import sys


def main() -> int:
    page, *pairs = sys.argv[1:]
    if len(pairs) % 2:
        raise SystemExit("notes come in (section, text) pairs")
    with open(page, encoding="utf-8") as f:
        doc = f.read()
    for section, text in zip(pairs[::2], pairs[1::2]):
        head = f"<h2>{html.escape(section, quote=True)}</h2>"
        if doc.count(head) != 1:
            raise SystemExit(f"section heading not found exactly once: {section!r}")
        doc = doc.replace(head, f'{head}\n<p class="intro">{html.escape(text)}</p>')
    with open(page, "w", encoding="utf-8") as f:
        f.write(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
