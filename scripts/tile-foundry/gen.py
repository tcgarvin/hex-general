#!/usr/bin/env python3
"""Generate an image with Gemini image models on Vertex AI, keyless.

Standalone CLI: builds a `generateContent` request (optional reference images
plus a text prompt), posts it to the Vertex AI endpoint using a short-lived
access token from the impersonated `gcloud` identity (see repo CLAUDE.md —
never a service-account JSON key), and saves the first inlineData image part
of the response.

Example:
    python3 gen.py --prompt "seamless grass texture, top-down" \\
        --ref concept.png --out swatch.png
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

PROJECT = "hex-general"
LOCATION = "global"
DEFAULT_MODEL = "gemini-3-pro-image-preview"
REQUEST_TIMEOUT_S = 300.0

_REF_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def get_access_token() -> str:
    """Fetch a short-lived access token via the impersonated `gcloud` identity."""
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def mime_for(path: Path) -> str:
    """Map a reference image's extension to an inlineData MIME type."""
    try:
        return _REF_MIME_TYPES[path.suffix.lower()]
    except KeyError:
        raise ValueError(
            f"unsupported reference image extension {path.suffix!r} for {path} "
            f"(supported: {sorted(_REF_MIME_TYPES)})"
        ) from None


def build_request(prompt: str, refs: list[Path], aspect: str, image_size: str) -> dict:
    """Build the Vertex `generateContent` request body: ref images, then the prompt text."""
    parts: list[dict] = []
    for ref in refs:
        data = base64.b64encode(ref.read_bytes()).decode("ascii")
        parts.append({"inlineData": {"mimeType": mime_for(ref), "data": data}})
    parts.append({"text": prompt})
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": aspect, "imageSize": image_size},
        },
    }


def call_vertex(model: str, body: dict, token: str) -> dict:
    """POST to the Vertex `generateContent` endpoint. Raises SystemExit on HTTP error."""
    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}"
        f"/publishers/google/models/{model}:generateContent"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"Vertex API error {exc.code}:\n{error_body}", file=sys.stderr)
        raise SystemExit(1) from exc


def _redact_inline_data(obj):
    """Recursively strip base64 blobs from a response so errors print readably."""
    if isinstance(obj, dict):
        return {
            k: (f"<{len(v)} base64 chars omitted>" if k == "data" and isinstance(v, str) else _redact_inline_data(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_inline_data(v) for v in obj]
    return obj


def extract_image(response: dict) -> bytes:
    """Pull the first inlineData image part out of a `generateContent` response.

    If none is present, prints the (base64-stripped) response for debugging
    and exits nonzero — a response with no image is a hard failure here, not
    a state to paper over.
    """
    for candidate in response.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData")
            if inline is not None:
                return base64.b64decode(inline["data"])
    print(json.dumps(_redact_inline_data(response), indent=2), file=sys.stderr)
    raise SystemExit(1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate an image via Vertex Gemini image models.")
    prompt_group = ap.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="inline prompt text")
    prompt_group.add_argument("--prompt-file", type=Path, help="path to a file containing the prompt")
    ap.add_argument(
        "--ref",
        action="append",
        default=[],
        type=Path,
        dest="refs",
        help="reference image path (repeatable; png/jpg/jpeg/webp)",
    )
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"model id (default {DEFAULT_MODEL})")
    ap.add_argument("--image-size", default="2K", choices=["1K", "2K", "4K"], help="default 2K")
    ap.add_argument("--aspect", default="1:1", help="aspect ratio (default 1:1)")
    ap.add_argument("--out", required=True, type=Path, help="output PNG path")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text()

    for ref in args.refs:
        if not ref.is_file():
            print(f"reference image not found: {ref}", file=sys.stderr)
            return 1
        try:
            mime_for(ref)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1

    token = get_access_token()
    body = build_request(prompt, args.refs, args.aspect, args.image_size)
    response = call_vertex(args.model, body, token)
    image_bytes = extract_image(response)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(image_bytes)
    with Image.open(args.out) as img:
        width, height = img.size

    usage = response.get("usageMetadata", {})
    print(f"saved {args.out} ({width}x{height})")
    print(f"usageMetadata: {usage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
