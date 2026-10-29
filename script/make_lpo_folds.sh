set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
N="${1:-10}"
DATA="${2:-$ROOT/member_graphs}"
export PYTHONPATH="${ROOT}/evaluation:${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
"$PY" "$ROOT/evaluation/pipeline.py" --dataset "$DATA" lpo --n-folds "$N"
