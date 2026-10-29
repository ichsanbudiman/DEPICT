set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/sample_project/Nutch v0.4"
OUT="${1:-$ROOT/dataset}"
bash "$ROOT/script/extract_project.sh" "$SRC" "$OUT"
