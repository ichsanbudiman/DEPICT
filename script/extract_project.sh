#!/usr/bin/env bash
# Usage: bash script/extract_project.sh <java_project_dir> [output_root]
# Then convert with: bash script/from_sgr.sh [output_root]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:?java project path required}"
OUT="${2:-$ROOT/dataset}"
export PYTHONPATH="${ROOT}/sgr_extractor${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" -m symbolic_extractors.multifile.subgraph_dataset \
  --project "$SRC" \
  --output "$OUT" \
  --mode project
echo "SGR written under $OUT"
echo "Convert: bash $ROOT/script/from_sgr.sh $OUT"
