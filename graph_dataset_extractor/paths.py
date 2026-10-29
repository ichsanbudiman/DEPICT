from __future__ import annotations

from pathlib import Path

PKG = Path(__file__).resolve().parent
DEPICT_ROOT = PKG.parent
SGR_EXTRACTOR = DEPICT_ROOT / "sgr_extractor"

def _sgr_root() -> Path:
    """SGR directory. ``SGR_ROOT`` in ``.env`` wins; otherwise ``<repo>/dataset``."""
    import os

    env = DEPICT_ROOT / ".env"
    if env.is_file():
        from envfile import load_depict_env

        load_depict_env()
    raw = os.environ.get("SGR_ROOT", "").strip()
    if not raw:
        return DEPICT_ROOT / "dataset"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return (DEPICT_ROOT / path).resolve()
    return path


# Each project is <SGR_ROOT>/<Project>/project_sgr.yaml
SGR_ROOT = _sgr_root()
PROJECT_SGR_FILE = "project_sgr.yaml"


def _java_projects() -> Path | None:
    """Java checkouts. ``JAVA_PROJECTS`` in ``.env``. Unset leaves snippets empty."""
    import os

    raw = os.environ.get("JAVA_PROJECTS", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return (DEPICT_ROOT / path).resolve()
    return path


JAVA_PROJECTS = _java_projects()

# graph.json and features/ are written beside <Project>/project_sgr.yaml
GRAPH_FILE = "graph.json"
MEMBER_OUT = SGR_ROOT

GOLD_DIR = PKG / "gold"
GOLD_CSV = GOLD_DIR / "dpd_graph_v3_bplus_balanced_labels_gt100.csv"
GOLD_ADAPTER_AS_FACADE = GOLD_DIR / "gold_adapter_as_facade.txt"
GOLD_ADAPTER_WRONG_ROOT = GOLD_DIR / "gold_adapter_wrong_root.txt"

LOGS_DIR = PKG / "logs"
SPINE_OVERLAY = PKG / "gold_spine_clean.csv"

# Names the copied converters still import
V5_ROOT = PKG
DATASET = MEMBER_OUT
SAMPLE = SGR_ROOT
REPO_ROOT = DEPICT_ROOT


def ensure_sgr_on_path() -> None:
    import sys

    root = str(SGR_EXTRACTOR)
    if root not in sys.path:
        sys.path.insert(0, root)
