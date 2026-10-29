set -euo pipefail
SRC="${1:?java project path required}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SGR="${2:-}"
OUT="${3:-}"
HOPS="${4:-1}"
NEIGH="${5:-v2}"
export PYTHONPATH="${ROOT}/sgr_extractor:${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
ARGS=(--hops "$HOPS" --neighbourhood "$NEIGH")
if [[ -n "$SGR" ]]; then
  ARGS+=(--sgr-root "$SGR")
fi
if [[ -n "$OUT" ]]; then
  ARGS+=(--out "$OUT")
fi
"$PY" "$ROOT/graph_dataset_extractor/pipeline.py" \
  "${ARGS[@]}" \
  all --project "$SRC" --skip-stamp
