#!/bin/bash -l

#SBATCH --job-name=extract_sgr
#SBATCH --account=minkull-spdisc-ichsan
#SBATCH --qos=bbdefault
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --mail-user=ixb402@student.bham.ac.uk
#SBATCH --mail-type=END,FAIL
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err

set -e
export TERM=dumb
rm -rf ~/.cache/lmod ~/.lmod.d/__cache__ 2>/dev/null || true

module purge
set +e
module load bluebear
_BLUEBEAR_RC=$?
set -e

if [ -n "${SLURM_SUBMIT_DIR:-}" ]; then
  BASE_DIR="$SLURM_SUBMIT_DIR"
else
  BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$BASE_DIR" || {
  echo "ERROR: cannot cd to $BASE_DIR"
  exit 1
}
mkdir -p "$BASE_DIR/logs"

_DEFAULT_RDS_ROOT="/rds/projects/m/minkull-spdisc-ichsan/ichsan"
BEAR_APPS_VERSION="${BEAR_APPS_VERSION:-2023a}"
PYTHON_MODULE="${PYTHON_MODULE:-Python/3.11.3-GCCcore-12.3.0}"

_enable_bear_apps_mpath() {
  local ver="$1"
  local p
  for p in \
    "/rds/bear-apps/${ver}/modules/all" \
    "/rds/bear-apps/${ver}/el8/modules/all" \
    "/rds/bear-apps/${ver}/live/modules/all"
  do
    if [ -d "$p" ]; then
      echo "[INFO] enabling MODULEPATH $p"
      module use "$p"
      return 0
    fi
  done
  return 1
}

if [ "${_BLUEBEAR_RC}" -ne 0 ]; then
  echo "[WARN] module load bluebear failed (rc=${_BLUEBEAR_RC}); continuing with an explicit bear-apps path"
  _enable_bear_apps_mpath "${BEAR_APPS_VERSION}" || true
fi

_BEAR_CANDIDATES=("${BEAR_APPS_VERSION}" 2023b 2024a 2022b 2022a)
BEAR_LOADED=""
for ver in "${_BEAR_CANDIDATES[@]}"; do
  echo "[INFO] Trying: module load bear-apps/${ver}"
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
  echo "[ERROR] Could not module load any bear-apps version" >&2
  exit 1
fi
echo "[INFO] Loaded bear-apps/${BEAR_LOADED}"

_PYTHON_CANDIDATES=(
  "${PYTHON_MODULE}"
  "Python/3.11.3-GCCcore-12.3.0"
  "Python/3.10.8-GCCcore-12.2.0"
  "Python/3.10.4-GCCcore-11.3.0"
)
PYTHON_LOADED=""
for cand in "${_PYTHON_CANDIDATES[@]}"; do
  echo "[INFO] Trying: module load ${cand}"
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
  echo "[ERROR] Could not load a Python module under bear-apps/${BEAR_LOADED}" >&2
  exit 1
fi
echo "[INFO] Python module: ${PYTHON_LOADED}"
echo "[INFO] python3: $(command -v python3) ($(python3 --version 2>&1))"

_BB_CPU_TAG="${BB_CPU:-generic}"
export DEPICT_VENV="${DEPICT_VENV:-${_DEFAULT_RDS_ROOT}/venvs/depict-${_BB_CPU_TAG}}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/scratch/${USER:-u}/pip}"
export TMPDIR="${TMPDIR:-/tmp/${USER:-u}_depict_${SLURM_JOB_ID:-$$}}"
mkdir -p "$PIP_CACHE_DIR" "$TMPDIR" "$(dirname "$DEPICT_VENV")"

MODULE_PY="$(command -v python3)"
MODULE_PY_VER="$("$MODULE_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "[INFO] DEPICT_VENV: $DEPICT_VENV"

_VENV_LOCK="${DEPICT_VENV}.setup.lock"
PIP_LOG="${TMPDIR}/pip_install_depict.log"
(
  flock -w 1800 9 || {
    echo "[ERROR] Timed out waiting for venv lock: $_VENV_LOCK" >&2
    exit 1
  }

  _venv_broken() {
    [ ! -x "${DEPICT_VENV}/bin/python" ] && return 0
    "${DEPICT_VENV}/bin/python" -m pip --version >/dev/null 2>&1 || return 0
    return 1
  }

  _recreate_venv() {
    echo "[INFO] Recreating venv at $DEPICT_VENV"
    rm -rf "$DEPICT_VENV"
    "$MODULE_PY" -m venv --system-site-packages "$DEPICT_VENV"
    "${DEPICT_VENV}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  }

  _VENV_PY_VER="none"
  if [ -x "${DEPICT_VENV}/bin/python" ]; then
    _VENV_PY_VER="$("${DEPICT_VENV}/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
  fi
  if [ ! -x "${DEPICT_VENV}/bin/python" ] || [ "$_VENV_PY_VER" != "$MODULE_PY_VER" ] || _venv_broken; then
    _recreate_venv
  fi

  # shellcheck disable=SC1091
  source "${DEPICT_VENV}/bin/activate"
  export PATH="${DEPICT_VENV}/bin:${PATH}"
  unset PYTHONUSERBASE || true
  export PIP_USER=0

  MARKER="${DEPICT_VENV}/.depict_req_installed"
  NEED_INSTALL=0
  if [ "${FORCE_PIP_INSTALL:-0}" = "1" ] || [ ! -f "$MARKER" ]; then
    NEED_INSTALL=1
  elif ! python -c "import yaml, tree_sitter, tree_sitter_java" >/dev/null 2>&1; then
    echo "[WARN] venv marker is present but imports failed; reinstalling"
    NEED_INSTALL=1
  fi

  if [ "$NEED_INSTALL" -eq 1 ]; then
    echo "[INFO] Installing requirements into $DEPICT_VENV"
    set +e
    python -m pip install --upgrade -r "${BASE_DIR}/requirements.txt" >"$PIP_LOG" 2>&1
    PIP_EXIT_CODE=$?
    set -e
    if [ "$PIP_EXIT_CODE" -ne 0 ]; then
      echo "[WARN] pip failed; recreating venv and retrying once"
      tail -40 "$PIP_LOG" || true
      deactivate 2>/dev/null || true
      _recreate_venv
      # shellcheck disable=SC1091
      source "${DEPICT_VENV}/bin/activate"
      export PATH="${DEPICT_VENV}/bin:${PATH}"
      export PIP_USER=0
      set +e
      python -m pip install --upgrade -r "${BASE_DIR}/requirements.txt" >"$PIP_LOG" 2>&1
      PIP_EXIT_CODE=$?
      set -e
    fi
    if [ "$PIP_EXIT_CODE" -ne 0 ]; then
      echo "[ERROR] pip install failed. Last 60 lines:" >&2
      tail -60 "$PIP_LOG" >&2 || true
      exit 1
    fi
    date -Iseconds >"$MARKER"
  else
    echo "[INFO] Reusing venv packages (FORCE_PIP_INSTALL=1 to refresh)"
  fi
) 9>"$_VENV_LOCK"

# shellcheck disable=SC1091
source "${DEPICT_VENV}/bin/activate"
export PATH="${DEPICT_VENV}/bin:${PATH}"
unset PYTHONUSERBASE || true
export PIP_USER=0
python -c "import yaml, tree_sitter, tree_sitter_java; print('deps ok: yaml+tree_sitter+tree_sitter_java')"

echo "=========================================="
echo "SGR EXTRACT (active gold projects)"
echo "=========================================="
echo "Job ID:  ${SLURM_JOB_ID:-local}"
echo "Node:    ${SLURM_NODELIST:-local}"
echo "Date:    $(date)"
echo "Repo:    $(pwd)"
echo "Python:  $(command -v python) ($(python --version 2>&1))"
echo "Summary: ${BASE_DIR}/logs/extract_sgr_summary.log"
echo "=========================================="

set +e
bash script/extract_active.sh
EXIT_CODE=$?
set -e

echo ""
echo "=========================================="
echo "DONE  exit=$EXIT_CODE  $(date)"
echo "=========================================="
exit "$EXIT_CODE"
