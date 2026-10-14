#!/usr/bin/env bash
# Usage: bash scripts/extract_project.sh <java_project_dir> [output_root]
set -euo pipefail
SRC="${1:?java project path required}"
OUT="${2:-dataset}"
python -m symbolic_extractors.multifile.subgraph_dataset \
  --project "$SRC" \
  --output "$OUT" \
  --mode project
