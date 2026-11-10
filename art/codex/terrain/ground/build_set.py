"""Build one ground candidate set from a Codex source: (flatten) -> recolour -> cut -> salience
filter -> seam clique (pick 6) -> gate report. Writes <set>/plains/00..05.png, <set>/source.png
(the corrected source the tiles are cut from) and <set>/set.json (all parameters and numbers).

Usage:
  python3 build_set.py <raw.png> --set <name> [--scale 1|2] [--flatten-sigma S] [--no-recolour]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from grain import blur, erode, image_metrics, luminance
from recolour import apply, auto_params

HERE = Path(__file__).resolve().parent
FOUNDRY = HERE.parents[3] / "scripts" / "tile-foundry"
sys.path.insert(0, str(FOUNDRY))
from gate import load_tile, mean_hex_luminance  # noqa: E402
PICK = 6
POOL = 20
SPOT_MAD_K = 3.5


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def spot_score(path: Path) -> float:
    """Largest local (sigma 3 px) luminance excursion from the tile median inside the hex:
    a salient blob, tuft or dark spot shows up as an outlier against the pool."""
    arr = np.asarray(Image.open(path))
    alpha = arr[..., 3] > 127
    lum = luminance(arr[..., :3].astype(np.float64))
    m = alpha.astype(np.float64)
    g = blur(lum * m, 3.0) / np.maximum(blur(m, 3.0), 1e-6)
    inner = erode(alpha, 6)
    return float(np.abs(g[inner] - np.median(lum[inner])).max())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", type=Path)
    ap.add_argument("--set", required=True)
    ap.add_argument("--scale", type=int, default=1, choices=[1, 2])
    ap.add_argument("--flatten-sigma", type=float, default=None)
    ap.add_argument("--no-recolour", action="store_true")
    args = ap.parse_args()

    out = HERE / args.set
    cand = out / "candidates"
    if out.exists():
        shutil.rmtree(out)
    cand.mkdir(parents=True)
    record: dict = {"raw": str(args.raw.resolve().relative_to(HERE.parent)), "scale": f"{args.scale}:1"}

    img = Image.open(args.raw).convert("RGB")
    if args.flatten_sigma is not None:
        flat = out / "flat.png"
        run([sys.executable, str(FOUNDRY / "flatten.py"), str(args.raw), "--sigma", str(args.flatten_sigma), "--out", str(flat)])
        img = Image.open(flat).convert("RGB")
        flat.unlink()
        record["flatten"] = {"tool": "scripts/tile-foundry/flatten.py", "sigma": args.flatten_sigma, "clip": [0.6, 1.8]}
    if args.scale == 2:
        img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
        record["resample"] = "LANCZOS to half size before cutting (256 px source window -> 128 px tile)"
    rgb = np.asarray(img, dtype=np.float64)
    if not args.no_recolour:
        params = auto_params(rgb, 1.0)
        rgb = apply(rgb, params)
        record["recolour"] = {"tool": "recolour.py --auto --strength 1.0", **params}
    src = out / "source.png"
    Image.fromarray(np.asarray(rgb, dtype=np.uint8)).save(src)

    run([sys.executable, str(FOUNDRY / "cut.py"), str(src), "--out-dir", str(cand), "--stride", "128", "--bleed", "1"])
    cands = sorted(cand.glob("cand_*.png"))
    spots = {p.name: spot_score(p) for p in cands}
    vals = np.array(list(spots.values()))
    med, mad = float(np.median(vals)), float(np.median(np.abs(vals - np.median(vals))))
    limit = med + SPOT_MAD_K * 1.4826 * mad
    keep = [p for p in cands if spots[p.name] <= limit]
    record["candidates"] = {"cut": "cut.py --stride 128 --bleed 1", "n": len(cands), "spot_limit": round(limit, 2),
                            "dropped_salient": sorted(p.name for p in cands if p not in keep)}

    # gate.py clique pools the POOL candidates nearest the mean luminance anyway; pre-pooling the
    # same way (gate.mean_hex_luminance) keeps the all-pairs seam gate at POOL^2 pairs, not N^2.
    lums = {p.name: mean_hex_luminance(load_tile(p, 128)) for p in keep}
    centre = float(np.mean(list(lums.values())))
    keep = sorted(keep, key=lambda p: abs(lums[p.name] - centre))[:POOL]
    record["candidates"]["pool"] = f"{POOL} nearest mean luminance {centre:.1f}"

    clique_out = run([sys.executable, str(FOUNDRY / "gate.py"), "clique", *map(str, keep), "--seam-mode", "texture",
                      "--pick", str(PICK), "--pool-size", str(POOL), "--out-dir", str(out / "plains")])
    pick_manifest = json.loads((out / "plains" / "manifest.json").read_text())
    (out / "plains" / "manifest.json").unlink()
    record["clique"] = {"cmd": f"gate.py clique --seam-mode texture --pick {PICK} --pool-size {POOL}",
                        "largest_compatible": clique_out.splitlines()[0],
                        "worst_pairwise_badness_texture": round(pick_manifest["worst_pairwise_metric"], 3)}
    record["tiles"] = []
    for t in pick_manifest["tiles"]:
        x, y = (int(s[1:]) for s in t["source"][len("cand_"):-len(".png")].split("_"))
        f = args.scale
        record["tiles"].append({"file": f"plains/{t['index']:02d}.png", "cut": t["source"],
                                "source_offset_px": [x, y], "raw_window_ltrb": [x * f, y * f, (x + 128) * f, (y + 128) * f],
                                "spot": round(spots[t["source"]], 2),
                                **image_metrics(str(out / "plains" / f"{t['index']:02d}.png"))})

    tiles = sorted((out / "plains").glob("0*.png"))
    gates = {}
    for mode in ("smooth", "texture"):
        rep = json.loads(run_ok([sys.executable, str(FOUNDRY / "gate.py"), *map(str, tiles), "--seam-mode", mode, "--json"]))
        seams = rep["seam"].values()
        gates[mode] = {"pass": rep["pass"], "worst_seam_ratio": round(max(v["worst_ratio"] for v in seams), 3),
                       "worst_trunc": round(max(v["score"] for v in rep["truncation"].values()), 3)}
        if mode == "texture":
            gates[mode]["worst_dc_over_sigma"] = round(max(abs(v["dc_step"]) / v["sigma_tex"] for v in seams), 3)
    record["gate"] = gates
    (out / "set.json").write_text(json.dumps(record, indent=1))
    print(json.dumps({k: record[k] for k in ("clique", "gate")}, indent=1))


def run_ok(cmd: list[str]) -> str:
    """gate.py exits 1 on a FAIL but still prints its JSON report, which we want either way."""
    return subprocess.run(cmd, capture_output=True, text=True).stdout


if __name__ == "__main__":
    main()
