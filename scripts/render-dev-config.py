#!/usr/bin/env python3
"""Render an instance-offset Supabase config from the committed base.

`scripts/dev.sh` calls this to produce `.dev/supabase/config.toml` for a
worktree's isolated stack: every host-bound port and the auth redirect origin
are shifted by ``100 * instance``, ``project_id`` is namespaced so the Docker
containers don't collide, and the lean services can be turned back on for a
"full" stack. The committed ``supabase/config.toml`` stays the single source of
truth (CI and a bare ``supabase start`` use it verbatim); this script only
rewrites the handful of instance-specific values, line by line, so it never has
to round-trip the whole TOML through a parser/emitter.

Usage:
    render-dev-config.py --src supabase/config.toml --instance N \
        --project-id ID --vite-port PORT [--full]
"""

from __future__ import annotations

import argparse
import re
import sys

STRIDE = 100

# Host-bound port keys that must be offset per instance. Every occurrence in the
# file is shifted; the committed config declares them all exactly once.
PORT_KEYS = ("port", "shadow_port", "inspector_port")

# Services disabled in the lean (default) stack. `--full` flips their
# `enabled = false` back to true. Keyed by the TOML section header.
LEAN_SECTIONS = frozenset({"realtime", "studio", "inbucket", "analytics"})

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")
_PORT_RE = re.compile(
    r"^(?P<lead>\s*(?:%s)\s*=\s*)(?P<num>\d+)\s*$" % "|".join(PORT_KEYS)
)
_PROJECT_RE = re.compile(r"^\s*project_id\s*=")
_ENABLED_FALSE_RE = re.compile(r"^(?P<lead>\s*enabled\s*=\s*)false\s*$")


def render(
    text: str, instance: int, project_id: str, vite_port: int, full: bool
) -> str:
    offset = STRIDE * instance
    out: list[str] = []
    section = ""
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1)
            out.append(line)
            continue

        if _PROJECT_RE.match(line):
            out.append(f'project_id = "{project_id}"')
            continue

        m = _PORT_RE.match(line)
        if m:
            out.append(f"{m.group('lead')}{int(m.group('num')) + offset}")
            continue

        # The auth origin allow-list points at the Vite dev server; retarget it
        # to this instance's Vite port (base 5173 in the committed file).
        if section == "auth" and "5173" in line:
            out.append(line.replace("5173", str(vite_port)))
            continue

        if full and section in LEAN_SECTIONS:
            m = _ENABLED_FALSE_RE.match(line)
            if m:
                out.append(f"{m.group('lead')}true")
                continue

        out.append(line)

    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="committed base config.toml")
    ap.add_argument("--instance", type=int, required=True, help="HEX_INSTANCE (>= 0)")
    ap.add_argument(
        "--project-id", required=True, help="per-worktree Supabase project_id"
    )
    ap.add_argument(
        "--vite-port", type=int, required=True, help="this instance's Vite port"
    )
    ap.add_argument("--full", action="store_true", help="re-enable the lean services")
    args = ap.parse_args()

    if args.instance < 0:
        ap.error("--instance must be >= 0")

    with open(args.src, encoding="utf-8") as fh:
        text = fh.read()

    sys.stdout.write(
        render(text, args.instance, args.project_id, args.vite_port, args.full)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
