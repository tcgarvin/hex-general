#!/usr/bin/env bash
# Run one Codex image-generation call for a terrain probe (docs/ART_PIPELINE.md, "Codex recipe").
# Usage: run_codex.sh <name> [reference.png ...]
#   reads prompts/<name>.txt, writes raw/<name>.png + raw/<name>.run.json;
#   reference images are attached to the prompt with codex exec -i.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
name=$1
shift
imgs=()
refs=()
for f in "$@"; do imgs+=(-i "$(realpath "$f")"); refs+=("$(realpath "$f")"); done
wd=$(mktemp -d)
start=$(date +%s)
cd "$wd"
codex exec --skip-git-repo-check -s read-only -C "$wd" "${imgs[@]}" --json - \
  < "$here/prompts/$name.txt" > run.jsonl 2> run.err
end=$(date +%s)
tid=$(head -1 run.jsonl | python3 -c 'import json,sys;print(json.load(sys.stdin)["thread_id"])')
src=$(ls ~/.codex/generated_images/$tid/*.png | head -1)
cp "$src" "$here/raw/$name.png"
python3 - "$here/raw/$name.run.json" "$tid" "$src" $((end-start)) "${refs[@]}" <<'PY'
import json, sys
out, tid, src, secs, *refs = sys.argv[1:]
json.dump({"codex_thread": tid, "codex_source": src, "wall_seconds": int(secs), "refs": refs},
          open(out, "w"), indent=1)
PY
echo "$name: thread $tid, $((end-start)) s"
