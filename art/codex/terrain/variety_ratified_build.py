#!/usr/bin/env python3
"""Build the ratified variety set (2026-11-11) and apply it to the frontend.

Collects the variety tiles the owner approved from the round 1 (small) and
round 2 (medium) kept candidates into `candidates/variety-ratified/`, laid out
exactly as the frontend loads them, byte for byte:

    plains_nature/s<NN>.png, m<NN>.png    small / medium plains features
    plains_home/s<NN>.png, m<NN>.png      small / medium homesteads
    road_variety/<shape>_s<N>.png, _m<N>  small / medium roadside, per road shape

writes `picks.json` (every ratified file -> round, label, kept source, work dir,
cell, raw, sha256), marks both rounds' `candidates.json` with the decision, and
copies the set into `frontend/src/board/tiles/` (`scripts/tests/
test_variety_tiles_sync.py` checks the copies).

Re-runnable: copies are overwritten with identical bytes. Fails loudly on a
missing source, a source whose sha256 differs from its candidates.json record,
a label that is not a kept candidate, or stray files in the target folders.

    python3 art/codex/terrain/variety_ratified_build.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CANDIDATES = HERE / "candidates"
RATIFIED = CANDIDATES / "variety-ratified"
FRONTEND_TILES = REPO / "frontend" / "src" / "board" / "tiles"
FOLDERS = ("plains_nature", "plains_home", "road_variety")
RATIFIED_ON = "2026-11-11"
OWNER_WORDS = (
    "I think the orchards and the rocky knolls are too much, too close to hills and "
    "forests. I leave density and small/medium mix to you, we can adjust later."
)
ROUNDS = {"small": "variety-r1", "medium": "variety-r2"}

# round -> labels approved, in the order they are numbered in the frontend
NATURE = {
    "small": [
        "nature-01", "nature-02", "nature-03", "nature-04", "nature-05", "nature-06",
        "nature-07", "nature-09", "nature-10", "nature-12", "nature-14",
    ],
    "medium": [
        "nature-m01", "nature-m02", "nature-m03", "nature-m04",
        "nature-m05", "nature-m08", "nature-m11", "nature-m12",
    ],
}  # fmt: skip
HOME = {
    "small": [
        "home-01", "home-02", "home-03", "home-04",
        "home-05", "home-06", "home-08", "home-10",
    ],
    "medium": [f"home-m{i:02d}" for i in range(1, 9)],
}  # fmt: skip
ROAD = {
    "small": [
        "road-straight_v-A", "road-straight_v-B", "road-straight_v-C",
        "road-straight_d-A", "road-straight_d-B", "road-straight_d-C",
        "road-turn_n_se-A", "road-turn_s_nw-A", "road-turn_s_nw-B",
        "road-turn_se_sw-A", "road-turn_se_sw-B", "road-turn_ne_nw-A", "road-turn_ne_nw-B",
    ],
    "medium": [
        "road-straight_v-mA", "road-straight_v-mB", "road-straight_d-mA", "road-straight_d-mB",
        "road-turn_n_se-mA", "road-turn_n_se-mB", "road-turn_s_nw-mA", "road-turn_se_sw-mA",
        "road-turn_ne_nw-mA", "road-fork_ne_nw_s-mA", "road-fork_se_n_sw-mA",
        "road-fork_se_n_sw-mB", "road-fork_se_nw_sw-mA", "road-fork_se_n_nw-mA",
        "road-fork_ne_n_s-mA", "road-fork_se_ne_nw-mA", "road-fork_se_nw_s-mA",
        "road-fork_se_n_s-mA",
    ],
}  # fmt: skip
# kept candidates left out, with the reason
EXCLUDED = {
    "small": {
        "nature-08": "not a first-pass pick (low bushes read weakly)",
        "nature-11": "owner direction: bright pale rock outcrop",
        "nature-13": "not a first-pass pick (dry-stone wall is a speck at zoom 1)",
        "nature-15": "not a first-pass pick (fence posts are a speck at zoom 1)",
        "home-07": "not a first-pass pick (walled yard close to the farm site)",
        "home-09": "not a first-pass pick (pale walls, brighter than the set)",
        "road-turn_n_se-B": "not a first-pass pick (kitchen garden in crop rows)",
    },
    "medium": {
        "nature-m06": "owner direction: rock outcrop reads as a rocky mound, too close to hills",
        "nature-m07": "owner direction: rocky knoll, too close to hills",
        "nature-m09": "owner direction: orchard, too close to forests",
        "nature-m10": "owner direction: orchard, too close to forests",
    },
}


class RatifyError(RuntimeError):
    """The ratified set cannot be built as recorded."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kept(size: str) -> dict[str, dict[str, Any]]:
    spec = json.loads((CANDIDATES / ROUNDS[size] / "candidates.json").read_text())
    return {c["label"]: c for c in spec["candidates"] if c["kept"]}


def plan() -> list[tuple[str, str, str]]:
    """(target file under RATIFIED, size, label) for every ratified tile."""
    out: list[tuple[str, str, str]] = []
    for size in ("small", "medium"):
        tag = size[0]
        for folder, labels in (("plains_nature", NATURE), ("plains_home", HOME)):
            for i, label in enumerate(labels[size]):
                out.append((f"{folder}/{tag}{i + 1:02d}.png", size, label))
        per_shape: dict[str, int] = {}
        for label in ROAD[size]:
            shape = label.split("-")[1]
            per_shape[shape] = per_shape.get(shape, 0) + 1
            out.append(
                (f"road_variety/{shape}_{tag}{per_shape[shape]}.png", size, label)
            )
    return out


def main() -> int:
    picks = []
    for size in ("small", "medium"):
        labels = set(NATURE[size] + HOME[size] + ROAD[size])
        overlap = labels & set(EXCLUDED[size])
        if overlap:
            raise RatifyError(
                f"{size}: labels both ratified and excluded: {sorted(overlap)}"
            )
        unknown = (labels | set(EXCLUDED[size])) - set(kept(size))
        if unknown:
            raise RatifyError(f"{size}: not kept candidates: {sorted(unknown)}")
        undecided = set(kept(size)) - labels - set(EXCLUDED[size])
        if undecided:
            raise RatifyError(
                f"{size}: kept candidates with no decision: {sorted(undecided)}"
            )
    for folder in FOLDERS:
        if (RATIFIED / folder).exists():
            shutil.rmtree(RATIFIED / folder)
    for target, size, label in plan():
        c = kept(size)[label]
        source = CANDIDATES / ROUNDS[size] / c["tile"]
        if sha256(source) != c["sha256"]:
            raise RatifyError(
                f"{label}: {source} differs from its candidates.json sha256"
            )
        dst = RATIFIED / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dst)
        picks.append(
            {
                "file": target,
                "size": size,
                "round": ROUNDS[size],
                "label": label,
                "source": str(source.relative_to(REPO)),
                "work": c["work"],
                "cell": c["cell"],
                "base": c["base"],
                "raw": c["raw"],
                "codex_thread": c["codex_thread"],
                "sha256": sha256(dst),
            }
        )
    (RATIFIED / "picks.json").write_text(
        json.dumps(
            {
                "ratified": RATIFIED_ON,
                "owner": OWNER_WORDS,
                "excluded": EXCLUDED,
                "picks": picks,
            },
            indent=1,
        )
        + "\n"
    )
    for size, round_dir in ROUNDS.items():
        path = CANDIDATES / round_dir / "candidates.json"
        spec = json.loads(path.read_text())
        ratified = {p["label"]: p["file"] for p in picks if p["size"] == size}
        spec["status"] = {
            "ratified": RATIFIED_ON,
            "owner": OWNER_WORDS,
            "applied_as": f"frontend/src/board/tiles/ ({', '.join(FOLDERS)}), via candidates/variety-ratified/",
            "ratified_labels": ratified,
            "excluded": EXCLUDED[size],
        }
        for cand in spec["candidates"]:
            if cand["kept"]:
                cand["ratified"] = cand["label"] in ratified
        path.write_text(json.dumps(spec, indent=1) + "\n")
    for folder in FOLDERS:
        dst = FRONTEND_TILES / folder
        if dst.exists():
            stray = {p.name for p in dst.iterdir()} - {
                p.name for p in (RATIFIED / folder).iterdir()
            }
            if stray:
                raise RatifyError(
                    f"{dst}: files not in the ratified set: {sorted(stray)}"
                )
            shutil.rmtree(dst)
        shutil.copytree(RATIFIED / folder, dst)
    print(
        f"ratified {len(picks)} tiles into {RATIFIED.relative_to(REPO)} and "
        f"{FRONTEND_TILES.relative_to(REPO)}/{{{','.join(FOLDERS)}}}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
