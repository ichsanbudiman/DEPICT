set -euo pipefail
SRC="${1:?java project path required}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SGR="${2:-$ROOT/dataset}"
OUT="${3:-$ROOT/member_graphs}"
HOPS="${4:-1}"
NEIGH="${5:-v1}"
export PYTHONPATH="${ROOT}/sgr_extractor:${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" "$ROOT/graph_dataset_extractor/pipeline.py" \
  --sgr-root "$SGR" \
  --out "$OUT" \
  --hops "$HOPS" \
  --neighbourhood "$NEIGH" \
  all --project "$SRC" --skip-stamp
