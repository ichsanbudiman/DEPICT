set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SGR="${1:-}"
OUT="${2:-$ROOT/member_graphs}"
FOLDER="${3:-}"
HOPS="${4:-1}"
NEIGH="${5:-v1}"
export PYTHONPATH="${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
ARGS=(--out "$OUT" --hops "$HOPS" --neighbourhood "$NEIGH")
if [[ -n "$SGR" ]]; then
  ARGS+=(--sgr-root "$SGR")
fi
if [[ -n "$FOLDER" ]]; then
  ARGS+=(--project-folder "$FOLDER")
fi
ARGS+=(convert)
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" "$ROOT/graph_dataset_extractor/pipeline.py" "${ARGS[@]}"
