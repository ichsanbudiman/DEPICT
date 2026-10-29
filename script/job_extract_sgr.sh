#!/bin/bash -l

#SBATCH --account=minkull-spdisc-ichsan
#SBATCH --job-name=extract_sgr
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=24:00:00
#SBATCH --output=logs/extract_sgr_%j.out
#SBATCH --error=logs/extract_sgr_%j.err

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
mkdir -p logs

echo "=========================================="
echo "SGR EXTRACT (active gold projects)"
echo "=========================================="
echo "Job ID:  ${SLURM_JOB_ID:-local}"
echo "Node:    ${SLURM_NODELIST:-local}"
echo "Date:    $(date)"
echo "Repo:    $(pwd)"
echo "Python:  $(command -v python3)  $(python3 --version 2>&1)"
echo "Summary: ${PROJECT_DIR}/logs/extract_sgr_summary.log"
echo "=========================================="

bash script/extract_active.sh
EXIT_CODE=$?

echo ""
echo "=========================================="
echo "DONE  exit=$EXIT_CODE  $(date)"
echo "=========================================="
exit "$EXIT_CODE"
