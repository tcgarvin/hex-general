#!/usr/bin/env bash
# Run one Codex image-generation call for a columns sheet (docs/ART_PIPELINE.md, "Codex recipe").
# Usage: run_codex.sh <sheet-name> [reference.png ...]
#   reads prompts/<sheet>.txt, writes raw/<sheet>.png + raw/<sheet>.run.json;
#   reference images are attached to the prompt with codex exec -i (image edits).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
sheet=$1
shift
imgs=()
for f in "$@"; do imgs+=(-i "$(realpath "$f")"); done
wd=$(mktemp -d)
start=$(date +%s)
cd "$wd"
codex exec --skip-git-repo-check -s read-only -C "$wd" "${imgs[@]}" --json - \
  < "$here/prompts/$sheet.txt" > run.jsonl 2> run.err
end=$(date +%s)
tid=$(head -1 run.jsonl | python3 -c 'import json,sys;print(json.load(sys.stdin)["thread_id"])')
src=$(ls ~/.codex/generated_images/$tid/*.png | head -1)
cp "$src" "$here/raw/$sheet.png"
python3 - "$here/raw/$sheet.run.json" "$tid" "$src" $((end-start)) <<'PY'
import json, sys
out, tid, src, secs = sys.argv[1:]
json.dump({"codex_thread": tid, "codex_source": src, "wall_seconds": int(secs)}, open(out, "w"), indent=1)
PY
echo "$sheet: thread $tid, $((end-start)) s"
