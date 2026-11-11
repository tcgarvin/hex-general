#!/usr/bin/env bash
# Run one Codex image-generation call for a UI candidate sheet (docs/ART_PIPELINE.md, "Codex recipe").
# Usage: run_codex.sh <set> <name> [reference.png ...]
#   reads <set>/prompts/<name>.txt, writes <set>/raw/<name>.png + <set>/raw/<name>.run.json;
#   reference images are attached to the prompt with codex exec -i (style references).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
set_dir=$here/$1
name=$2
shift 2
imgs=()
refs=()
for f in "$@"; do
  imgs+=(-i "$(realpath "$f")")
  refs+=("$(realpath --relative-to="$here/../../.." "$f")")
done
wd=$(mktemp -d)
start=$(date +%s)
cd "$wd"
set +e
timeout 900 codex exec --skip-git-repo-check -s read-only -C "$wd" "${imgs[@]}" --json - \
  < "$set_dir/prompts/$name.txt" > run.jsonl 2> run.err
rc=$?
set -e
end=$(date +%s)
tid=$(head -1 run.jsonl | python3 -c 'import json,sys;print(json.load(sys.stdin)["thread_id"])')
shopt -s nullglob
srcs=(~/.codex/generated_images/$tid/*.png)
if [ "$rc" -ne 0 ] || [ ${#srcs[@]} -eq 0 ]; then
  echo "$name: FAILED rc=$rc images=${#srcs[@]} after $((end-start)) s (scratch $wd)" >&2
  tail -5 run.err >&2
  tail -3 run.jsonl >&2
  exit 1
fi
src=$(ls -t "${srcs[@]}" | head -1)
cp "$src" "$set_dir/raw/$name.png"
python3 - "$set_dir/raw/$name.run.json" "$tid" "$src" $((end-start)) ${#srcs[@]} "${refs[@]}" <<'PY'
import json, sys
out, tid, src, secs, n, *refs = sys.argv[1:]
json.dump({"codex_thread": tid, "codex_source": src, "wall_seconds": int(secs),
           "images_in_thread": int(n), "references": refs}, open(out, "w"), indent=1)
PY
echo "$name: thread $tid, $((end-start)) s"
