set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/sgr_extractor:${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
if [[ -n "${DEPICT_VENV:-}" && -x "${DEPICT_VENV}/bin/python" ]]; then
  PY="${DEPICT_VENV}/bin/python"
elif [[ -x "${ROOT}/venv/bin/python" ]]; then
  PY="${ROOT}/venv/bin/python"
else
  PY="python3"
fi
"$PY" "$ROOT/graph_dataset_extractor/extract_active.py" "$@"
