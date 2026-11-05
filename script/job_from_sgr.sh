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

# The depict-emerald venv was built with Python 3.11. Its python binary needs
# libpython3.11.so.1.0, which is provided by the Python module, not by the venv.
# Load that module before activating the venv. Python 3.10 does not supply it.
module purge
set +e
module load bluebear
_BLUEBEAR_RC=$?
set -e

_enable_bear_apps_mpath() {
  local ver="$1"
  local p
  for p in \
    "/rds/bear-apps/${ver}/modules/all" \
    "/rds/bear-apps/${ver}/el8/modules/all" \
    "/rds/bear-apps/${ver}/live/modules/all"
  do
    if [ -d "$p" ]; then
      module use "$p"
      return 0
    fi
  done
  return 1
}

if [ "${_BLUEBEAR_RC}" -ne 0 ]; then
  _enable_bear_apps_mpath "2023a" || true
fi

BEAR_LOADED=""
for ver in 2023a 2023b 2024a 2022b 2022a; do
  set +e
  module load "bear-apps/${ver}"
  _BRC=$?
  set -e
  if [ "${_BRC}" -ne 0 ] && _enable_bear_apps_mpath "$ver"; then
    set +e
    module load "bear-apps/${ver}"
    _BRC=$?
    set -e
  fi
  if [ "${_BRC}" -eq 0 ]; then
    BEAR_LOADED="${ver}"
    break
  fi
done
if [ -z "${BEAR_LOADED}" ]; then
  echo "ERROR: could not load bear-apps" >&2
  exit 1
fi

PYTHON_LOADED=""
for cand in \
  "Python/3.11.3-GCCcore-12.3.0" \
  "Python/3.10.8-GCCcore-12.2.0" \
  "Python/3.10.4-GCCcore-11.3.0"
do
  set +e
  module load "${cand}"
  _PRC=$?
  set -e
  if [ "${_PRC}" -eq 0 ] && command -v python3 >/dev/null 2>&1; then
    PYTHON_LOADED="${cand}"
    break
  fi
done
if [ -z "${PYTHON_LOADED}" ]; then
  echo "ERROR: could not load a Python module under bear-apps/${BEAR_LOADED}" >&2
  exit 1
fi
echo "Python module: ${PYTHON_LOADED}"
set +e

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
