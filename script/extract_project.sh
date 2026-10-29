set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:?java project path required}"
OUT="${2:-}"
export PYTHONPATH="${ROOT}/sgr_extractor:${ROOT}/graph_dataset_extractor${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="python3"; fi
if [[ -z "$OUT" ]]; then
  OUT="$("$PY" -c "from paths import SGR_ROOT; print(SGR_ROOT)")"
fi
"$PY" -m symbolic_extractors.multifile.subgraph_dataset \
  --project "$SRC" \
  --output "$OUT" \
  --mode project
echo "SGR written under $OUT"
echo "Convert: bash $ROOT/script/from_sgr.sh $OUT"
