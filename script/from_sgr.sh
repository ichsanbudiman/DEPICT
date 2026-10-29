set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SGR="${1:-$ROOT/dataset}"
OUT="${2:-$ROOT/member_graphs}"
FOLDER="${3:-}"
HOPS="${4:-1}"
NEIGH="${5:-v1}"
export PYTHONPATH="${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
ARGS=(--sgr-root "$SGR" --out "$OUT" --hops "$HOPS" --neighbourhood "$NEIGH" convert)
if [[ -n "$FOLDER" ]]; then
  ARGS=(--sgr-root "$SGR" --out "$OUT" --project-folder "$FOLDER" --hops "$HOPS" --neighbourhood "$NEIGH" convert)
fi
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" "$ROOT/graph_dataset_extractor/pipeline.py" "${ARGS[@]}"
