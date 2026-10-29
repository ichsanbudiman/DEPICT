set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SGR="${1:-}"
OUT="${2:-}"
FOLDER="${3:-}"
HOPS="${4:-1}"
NEIGH="${5:-v2}"
JAVA_TEXT="${6:-}"
export PYTHONPATH="${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
ARGS=(--hops "$HOPS" --neighbourhood "$NEIGH")
if [[ "$JAVA_TEXT" == "1" || "$JAVA_TEXT" == "on" ]]; then
  ARGS+=(--java-text)
fi
if [[ -n "$SGR" ]]; then
  ARGS+=(--sgr-root "$SGR")
fi
if [[ -n "$OUT" ]]; then
  ARGS+=(--out "$OUT")
fi
if [[ -n "$FOLDER" ]]; then
  ARGS+=(--project-folder "$FOLDER")
fi
ARGS+=(convert)
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" "$ROOT/graph_dataset_extractor/pipeline.py" "${ARGS[@]}"
