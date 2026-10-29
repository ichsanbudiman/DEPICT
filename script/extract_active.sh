set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/sgr_extractor:${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" "$ROOT/graph_dataset_extractor/extract_active.py" "$@"
