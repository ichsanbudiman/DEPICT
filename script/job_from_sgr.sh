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
echo "Job ID:   ${SLURM_JOB_ID:-local}"
echo "Node:     ${SLURM_NODELIST:-local}"
echo "Date:     $(date)"
echo "Repo:     $(pwd)"
echo "Python:   $(command -v python3)  $(python3 --version 2>&1)"
echo "=========================================="

bash script/from_sgr.sh
EXIT_CODE=$?

echo ""
echo "=========================================="
echo "ANNOTATION FILES"
echo "=========================================="
python3 - <<'PY'
from pathlib import Path
root = Path("member_graphs")
files = sorted(root.glob("*/annotation.json"))
print("annotation files", len(files))
for path in files:
    print(path)
PY

echo ""
echo "=========================================="
echo "DONE  exit=$EXIT_CODE  $(date)"
echo "=========================================="
exit "$EXIT_CODE"
