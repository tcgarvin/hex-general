"""Assemble the sites round 5 candidate list (`candidates/sites-r5/candidates.json`).

Every gate-passing tile of the round's work dirs becomes a labelled candidate
(`factory-l1-A`, ...), with its source (work dir, cell, raw sheet), gate result,
occupied share (paint-in tiles; `sites_measure.py`) and whether it is KEPT for
review (`SELECTION` below, judged by eye at 3x and board scale; the reason for
every drop is recorded). Kept tiles are copied to `candidates/sites-r5/kept/`
with their sha256, so the review and a later apply read one fixed file.

usage: python3 art/codex/terrain/sites5_candidates.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sites_measure import measure_work  # noqa: E402

ROUND = HERE / "candidates" / "sites-r5"

# target -> (kind dir, [work dirs], is_cluster)
WORK = {
    "factory_l1": ("factory", ["r5-l1a"], False),
    "factory_l3": ("factory", ["r5-l3a", "r5-l3b"], False),
    "oil_l3": ("oil_field", ["r5-l3a", "r5-l3b"], False),
    "base_s1": ("base", ["r5-s1a", "r5-s1b", "r5-s1c"], True),
}
LABEL_PREFIX = {
    "factory_l1": "factory-l1",
    "factory_l3": "factory-l3",
    "oil_l3": "oil-l3",
    "base_s1": "base-s1",
}
# Kept cells in label order, then dropped gate-passing cells with the reason.
SELECTION: dict[str, dict[str, object]] = {
    "factory_l1": {
        "kept": ["l1_500", "l1_501", "l1_502", "l1_508"],
        "dropped": {
            "l1_503": "hall a little larger than the kept four (built .24)",
            "l1_504": "fine, but the four kept already cover the layouts; fewer to review",
            "l1_505": "hall a little larger than the kept four (built .23)",
            "l1_506": "fine, but close in layout to l1_500",
            "l1_507": "hall a little larger than the kept four (built .23)",
        },
    },
    "factory_l3": {
        "kept": ["l3_510", "l3_513", "l3_516", "l3_518"],
        "dropped": {
            "l3_514": "three halls stacked in one column, reads as the neat grid the owner rejected in round 4",
            "l3_517": "fine, but two halls nearly the same size; the kept four show the length spread better",
        },
    },
    "oil_l3": {
        "kept": ["l3_501", "l3_502", "l3_503", "l3_511", "l3_518"],
        "dropped": {},
    },
    "base_s1": {
        "kept": ["s1_n", "s1_o", "s1_q", "s1_m", "s1_h"],
        "dropped": {
            "s1_a": "parade ground is a ruler-edged rectangle on lawn; reads as a diagram",
            "s1_b": "parade ground is a ruler-edged rectangle on lawn; reads as a diagram",
            "s1_c": "parade ground is a ruler-edged rectangle on lawn; reads as a diagram",
            "s1_d": "parade ground is a ruler-edged rectangle on lawn; reads as a diagram",
            "s1_e": "parade ground is a ruler-edged rectangle on lawn; reads as a diagram",
            "s1_g": "trucks nearly as large as the hut",
            "s1_i": "trucks nearly as large as the hut",
            "s1_j": "trucks nearly as large as the hut; parade ground runs into the gate",
            "s1_k": "trucks and a crate pile crowd the west side",
            "s1_l": "trucks nearly as large as the hut",
            "s1_p": "trucks large, camp leans on the west bank",
            "s1_r": "fine, but close to s1_m with larger trucks",
        },
    },
}


STATUS = (
    "sites round 5 (2026-11-10); ratified 2026-11-11 (owner: 'proceed with site round 5, "
    "use your best judgment'), applied by sites_ratified_build.py"
)
# Kept label -> the file it became in candidates/sites-ratified/ (2026-11-11). Kept
# labels not listed were not used: oil-l3-B (bund squared to the hex rim) and
# base-s1-A, C, E (stage 1 keeps two flavours).
RATIFIED: dict[str, str] = {
    "factory-l1-A": "factory/l1_00.png",
    "factory-l1-B": "factory/l1_01.png",
    "factory-l1-C": "factory/l1_02.png",
    "factory-l1-D": "factory/l1_03.png",
    "factory-l3-A": "factory/l3_00.png",
    "factory-l3-B": "factory/l3_01.png",
    "factory-l3-C": "factory/l3_02.png",
    "factory-l3-D": "factory/l3_03.png",
    "oil-l3-A": "oil_field/l3_00.png",
    "oil-l3-C": "oil_field/l3_01.png",
    "oil-l3-D": "oil_field/l3_02.png",
    "oil-l3-E": "oil_field/l3_03.png",
    "base-s1-B": "base/stage1_a/h0.png",
    "base-s1-D": "base/stage1_b/h0.png",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(HERE.parents[2]))


def gather(target: str) -> dict[str, dict]:
    """cell -> record for every cell of the target's work dirs."""
    kind, works, cluster = WORK[target]
    out: dict[str, dict] = {}
    for w in works:
        work = ROUND / kind / w
        report = json.loads((work / "report.json").read_text())
        manifest = json.loads((work / "manifest.json").read_text())["runs"][-1]
        raw = work / "raw" / f"{manifest['name']}.png"
        if cluster:
            for name, c in report["clusters"].items():
                checks = c["members"]["h0"]["checks"]
                out[name] = {
                    "work": rel(work),
                    "cell": name,
                    "raw": rel(raw),
                    "tile": work / "tiles" / name / "h0.png",
                    "passed": c["passed"],
                    "gates": {k["name"]: k["value"] for k in checks},
                    "registration": {
                        "residual_tile_px": round(c["registration"]["residual_tile_px"], 3),
                        "iou": round(c["registration"]["iou"], 4),
                    },
                }
        else:
            measured = measure_work(work)
            for name, t in report["tiles"].items():
                out[name] = {
                    "work": rel(work),
                    "cell": name,
                    "raw": rel(raw),
                    "tile": work / "tiles" / f"{name}.png",
                    "passed": t["passed"],
                    "gates": {
                        k["name"]: k["value"] for k in t["checks"] if k["hard"]
                    },
                    "occupied": measured[name]["occupied"],
                    "built": measured[name]["built"],
                }
    return out


def main() -> int:
    spec_path = ROUND / "candidates.json"
    spec = json.loads(spec_path.read_text())
    kept_dir = ROUND / "kept"
    if kept_dir.exists():
        shutil.rmtree(kept_dir)
    candidates = []
    for target, sel in SELECTION.items():
        cells = gather(target)
        kept = list(sel["kept"])  # type: ignore[arg-type]
        dropped: dict[str, str] = sel["dropped"]  # type: ignore[assignment]
        passing = {n for n, c in cells.items() if c["passed"]}
        missing = passing - set(kept) - set(dropped)
        if missing:
            raise SystemExit(f"{target}: passing cells with no decision: {sorted(missing)}")
        for i, name in enumerate(kept):
            c = cells[name]
            if not c["passed"]:
                raise SystemExit(f"{target}: kept {name} did not pass the gates")
            label = f"{LABEL_PREFIX[target]}-{chr(ord('A') + i)}"
            dst = kept_dir / target / f"{label}.png"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(c["tile"], dst)
            candidates.append(
                {
                    "label": label,
                    "target": target,
                    "kept": True,
                    "ratified": RATIFIED.get(label),
                    "tile": str(dst.relative_to(ROUND)),
                    "sha256": sha(dst),
                    **{k: v for k, v in c.items() if k != "tile"},
                }
            )
        for name in sorted(dropped):
            c = cells[name]
            candidates.append(
                {
                    "label": None,
                    "target": target,
                    "kept": False,
                    "why_dropped": dropped[name],
                    **{k: v for k, v in c.items() if k != "tile"},
                }
            )
        failed = sorted(n for n, c in cells.items() if not c["passed"])
        spec["targets"][target]["cells_total"] = len(cells)
        spec["targets"][target]["cells_passed"] = len(passing)
        spec["targets"][target]["cells_failed_gates"] = failed
    unknown = set(RATIFIED) - {c["label"] for c in candidates if c["kept"]}
    if unknown:
        raise SystemExit(f"RATIFIED names labels that are not kept: {sorted(unknown)}")
    spec["round"] = STATUS
    spec["candidates"] = candidates
    spec_path.write_text(json.dumps(spec, indent=1) + "\n")
    print(f"wrote {rel(spec_path)}: {sum(c['kept'] for c in candidates)} kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
