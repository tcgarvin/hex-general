#!/usr/bin/env python3
"""Bundle montage/preview PNGs into one self-contained HTML review page.

Handing the user a `scripts/dev.sh montage up` localhost link doesn't work for
a claude.ai Artifact: Artifacts run under a strict CSP that blocks every
external request (no `http://` image src, no fonts, no scripts). This tool
inlines each image as a data: URI directly into the HTML fragment so the page
is a single file with zero outbound references — safe to hand to the
`Artifact` tool.

Usage:
    python3 scripts/review-page.py --title "T5 road production" \
        --out .dev/review-pages/t5-roads.html \
        --intro "Comparing NBP swatch-cut blends against the PixelLab bake." \
        --section "Plains straights" \
            "Candidates=road-candidates-plains.png" \
            .dev/montages/nbp-road-finish-hills_straight_d.png \
        --section "Board scale" \
            .dev/montages/nbp-road-blend-boardstrips-all.png

Each image arg is `path` or `label=path` (default label = filename stem).
`--section NAME` opens a new section; every image arg after it (up to the
next `--section` or end of argv) belongs to that section. There must be at
least one `--section` before any image arg.

Images wider than 1600px are downscaled to 1600px for embedding (these are
review montages, not delivered assets) and the caption notes the downscale.
Each image is encoded as PNG; if that PNG exceeds ~1.5 MB it is re-encoded as
JPEG quality 88 instead (flattened onto #2a2a2e first if it has alpha, since
JPEG has no alpha channel). Prints a per-image size report and the final HTML
size; warns loudly past ~18 MB (Artifact payload budget).

The output is a fragment ONLY — no <!DOCTYPE>/<html>/<head>/<body> — because
the `Artifact` tool wraps it. It does include a <title> and inline <style>,
per that tool's contract.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
from dataclasses import dataclass

from PIL import Image

MAX_EMBED_WIDTH = 1600
PNG_REENCODE_THRESHOLD = 1_500_000  # ~1.5 MB; above this, fall back to JPEG.
JPEG_QUALITY = 88
JPEG_FLATTEN_BG = (0x2A, 0x2A, 0x2E)
TOTAL_SIZE_WARN = 18 * 1024 * 1024  # ~18 MB Artifact budget.


@dataclass
class ImageSpec:
    """One `label=path` (or bare `path`) argument, not yet loaded."""

    label: str
    path: str


@dataclass
class Section:
    name: str
    images: list[ImageSpec]


@dataclass
class EncodedImage:
    label: str
    path: str
    data_uri: str
    mime: str
    display_size: tuple[int, int]
    original_size: tuple[int, int]
    encoded_bytes: int
    downscaled: bool


def parse_image_arg(arg: str) -> ImageSpec:
    """Split `label=path` on the first '=', else derive label from the stem."""
    if "=" in arg:
        label, path = arg.split("=", 1)
        if not label or not path:
            raise ValueError(f"malformed image arg (empty label or path): {arg!r}")
        return ImageSpec(label=label, path=path)
    stem = os.path.splitext(os.path.basename(arg))[0]
    return ImageSpec(label=stem, path=arg)


def parse_sections(argv: list[str]) -> tuple[argparse.Namespace, list[Section]]:
    """Manually split argv into (non-section options, [Section, ...]).

    argparse's `action="append", nargs="+"` can't interleave repeated
    `--section NAME img img img --section NAME2 img ...` groups with other
    named options in one pass, so we split argv ourselves: pull out
    `--section` and its trailing positional run as we walk the list, and feed
    everything else to a plain ArgumentParser.
    """
    ap = argparse.ArgumentParser(
        description="Bundle PNGs into one self-contained HTML review page."
    )
    ap.add_argument("--title", required=True, help="page <title> / <h1>")
    ap.add_argument("--out", required=True, help="output .html path")
    ap.add_argument("--intro", default="", help="one short paragraph under the title")

    plain_args: list[str] = []
    sections: list[Section] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--section":
            if i + 1 >= len(argv):
                raise ValueError("--section requires a name argument")
            name = argv[i + 1]
            i += 2
            images: list[ImageSpec] = []
            while i < len(argv) and argv[i] != "--section" and not argv[i].startswith("--"):
                images.append(parse_image_arg(argv[i]))
                i += 1
            if not images:
                raise ValueError(f"--section {name!r} has no images")
            sections.append(Section(name=name, images=images))
        else:
            plain_args.append(tok)
            i += 1

    if not sections:
        raise ValueError("at least one --section with images is required")

    args = ap.parse_args(plain_args)
    return args, sections


def load_and_encode(spec: ImageSpec) -> EncodedImage:
    """Load one image, downscale/re-encode as needed, and base64-embed it."""
    if not os.path.isfile(spec.path):
        raise FileNotFoundError(f"image not found: {spec.path}")

    img = Image.open(spec.path)
    img.load()
    original_size = img.size

    downscaled = False
    if img.width > MAX_EMBED_WIDTH:
        ratio = MAX_EMBED_WIDTH / img.width
        new_size = (MAX_EMBED_WIDTH, max(1, round(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)
        downscaled = True

    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG", optimize=True)
    png_bytes = png_buf.getvalue()

    if len(png_bytes) <= PNG_REENCODE_THRESHOLD:
        mime = "image/png"
        encoded_bytes = png_bytes
    else:
        # Re-encode as JPEG. Flatten alpha onto the montage's own dark
        # background colour first since JPEG has no alpha channel.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            flat = Image.new("RGB", img.size, JPEG_FLATTEN_BG)
            flat.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
            jpeg_source = flat
        else:
            jpeg_source = img.convert("RGB")
        jpeg_buf = io.BytesIO()
        jpeg_source.save(jpeg_buf, format="JPEG", quality=JPEG_QUALITY)
        mime = "image/jpeg"
        encoded_bytes = jpeg_buf.getvalue()

    b64 = base64.b64encode(encoded_bytes).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"

    return EncodedImage(
        label=spec.label,
        path=spec.path,
        data_uri=data_uri,
        mime=mime,
        display_size=img.size,
        original_size=original_size,
        encoded_bytes=len(encoded_bytes),
        downscaled=downscaled,
    )


def html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_html(title: str, intro: str, sections: list[tuple[Section, list[EncodedImage]]]) -> str:
    parts: list[str] = []
    parts.append(f"<title>{html_escape(title)}</title>")
    parts.append(STYLE)
    parts.append('<div class="review-page">')
    parts.append(f"<h1>{html_escape(title)}</h1>")
    if intro:
        parts.append(f"<p class=\"intro\">{html_escape(intro)}</p>")

    for section, encoded_images in sections:
        parts.append(f"<h2>{html_escape(section.name)}</h2>")
        for enc in encoded_images:
            caption = html_escape(enc.label)
            if enc.downscaled:
                ow, oh = enc.original_size
                dw, dh = enc.display_size
                caption += f" &mdash; shown at {dw}&times;{dh}px (original {ow}&times;{oh}px)"
            parts.append(
                '<figure class="review-image">'
                '<div class="image-scroll">'
                f'<img src="{enc.data_uri}" alt="{html_escape(enc.label)}" '
                'onclick="this.classList.toggle(\'natural-size\')">'
                "</div>"
                f"<figcaption>{caption}</figcaption>"
                "</figure>"
            )

    parts.append("</div>")
    return "\n".join(parts)


STYLE = """<style>
  :root {
    color-scheme: light dark;
    --rp-bg: #ffffff;
    --rp-fg: #1a1a1e;
    --rp-muted: #5a5a63;
    --rp-border: #d8d8dd;
    --rp-image-bg: #2a2a2e;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --rp-bg: #16161a;
      --rp-fg: #e8e8ec;
      --rp-muted: #a0a0aa;
      --rp-border: #33333a;
      --rp-image-bg: #2a2a2e;
    }
  }
  :root[data-theme="dark"] {
    --rp-bg: #16161a;
    --rp-fg: #e8e8ec;
    --rp-muted: #a0a0aa;
    --rp-border: #33333a;
    --rp-image-bg: #2a2a2e;
  }
  :root[data-theme="light"] {
    --rp-bg: #ffffff;
    --rp-fg: #1a1a1e;
    --rp-muted: #5a5a63;
    --rp-border: #d8d8dd;
    --rp-image-bg: #2a2a2e;
  }
  body {
    background: var(--rp-bg);
    color: var(--rp-fg);
  }
  .review-page {
    max-width: 1100px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
      Arial, sans-serif;
    line-height: 1.5;
  }
  .review-page h1 {
    font-size: 1.6rem;
    margin-bottom: 0.25rem;
  }
  .review-page .intro {
    color: var(--rp-muted);
    margin-top: 0;
    margin-bottom: 2rem;
  }
  .review-page h2 {
    font-size: 1.15rem;
    margin-top: 2.5rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid var(--rp-border);
  }
  .review-image {
    margin: 1.25rem 0;
  }
  .image-scroll {
    overflow-x: auto;
    background: var(--rp-image-bg);
    border-radius: 6px;
    padding: 0.5rem;
  }
  .review-image img {
    display: block;
    max-width: 100%;
    height: auto;
    cursor: zoom-in;
    border-radius: 2px;
  }
  .review-image img.natural-size {
    max-width: none;
    cursor: zoom-out;
  }
  .review-image figcaption {
    margin-top: 0.5rem;
    font-size: 0.85rem;
    color: var(--rp-muted);
  }
</style>"""


def main() -> int:
    args, sections = parse_sections(sys.argv[1:])

    encoded_sections: list[tuple[Section, list[EncodedImage]]] = []
    total_bytes = 0
    print("per-image report:")
    for section in sections:
        encoded_images: list[EncodedImage] = []
        for spec in section.images:
            enc = load_and_encode(spec)
            encoded_images.append(enc)
            total_bytes += enc.encoded_bytes
            note = ""
            if enc.downscaled:
                ow, oh = enc.original_size
                dw, dh = enc.display_size
                note = f", downscaled {ow}x{oh} -> {dw}x{dh}"
            print(
                f"  [{section.name}] {enc.label}: {enc.mime}, "
                f"{enc.encoded_bytes / 1024:.1f} KiB{note}"
            )
        encoded_sections.append((section, encoded_images))

    html = build_html(args.title, args.intro, encoded_sections)
    html_bytes = len(html.encode("utf-8"))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nembedded image bytes: {total_bytes / 1024 / 1024:.2f} MiB")
    print(f"final HTML: {args.out}  ({html_bytes / 1024 / 1024:.2f} MiB)")
    if html_bytes > TOTAL_SIZE_WARN:
        print(
            f"WARNING: output exceeds ~{TOTAL_SIZE_WARN / 1024 / 1024:.0f} MiB "
            "Artifact budget — trim images or split into multiple pages.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
