#!/bin/bash -l

#SBATCH --account=minkull-spdisc-ichsan
#SBATCH --job-name=from_sgr
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=12:00:00
#SBATCH --output=logs/from_sgr_%j.out
#SBATCH --error=logs/from_sgr_%j.err

set -uo pipefail
export TERM=dumb
rm -rf ~/.cache/lmod ~/.lmod.d/__cache__ 2>/dev/null || true
module purge
module load bluebear
module load bear-apps/2022a 2>/dev/null || true
module load Python/3.10.4-GCCcore-11.3.0 2>/dev/null || true

PROJECT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" || {
  echo "ERROR: cannot cd to $PROJECT_DIR"
  exit 1
}

echo "=========================================="
echo "GRAPH DATASET EXTRACTOR"
echo "=========================================="
HOPS=1
NEIGH=v2
# 1 reads snippet text from JAVA_PROJECTS. Leave 0 when metadata.yaml already has sources.
JAVA_TEXT=0
SUMMARY="${PROJECT_DIR}/logs/extract_sgr_summary.log"
_BB_CPU_TAG="${BB_CPU:-generic}"
export DEPICT_VENV="${DEPICT_VENV:-/rds/projects/m/minkull-spdisc-ichsan/ichsan/venvs/depict-${_BB_CPU_TAG}}"
if [[ -x "${DEPICT_VENV}/bin/python" ]]; then
  # shellcheck disable=SC1091
  source "${DEPICT_VENV}/bin/activate"
  export PATH="${DEPICT_VENV}/bin:${PATH}"
fi
echo "Job ID:        ${SLURM_JOB_ID:-local}"
echo "Node:          ${SLURM_NODELIST:-local}"
echo "Date:          $(date)"
echo "Repo:          $(pwd)"
echo "Python:        $(command -v python3)  $(python3 --version 2>&1)"
echo "Neighbourhood: ${NEIGH}"
echo "Hops:          ${HOPS}"
echo "Java text:     ${JAVA_TEXT}"
echo "SGR summary:   ${SUMMARY}"
echo "Graph summary: ${PROJECT_DIR}/logs/from_sgr_summary.log"
echo "=========================================="

bash script/from_sgr.sh "" "" "" "$HOPS" "$NEIGH" "$JAVA_TEXT" "$SUMMARY"
EXIT_CODE=$?

echo ""
echo "=========================================="
echo "GRAPH FILES"
echo "=========================================="
python3 - <<'PY'
from pathlib import Path
root = Path("dataset")
files = sorted(root.glob("*/graph.json"))
print("graph files", len(files))
for path in files:
    print(path)
PY

echo ""
echo "=========================================="
echo "DONE  exit=$EXIT_CODE  $(date)"
echo "=========================================="
exit "$EXIT_CODE"
