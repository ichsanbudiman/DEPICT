from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def save_yaml(path: str | Path, data: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False, allow_unicode=True)


def sgr_relpath(java_path: str | Path, source_root: str | Path) -> Path:
    path = Path(java_path).resolve()
    root = Path(source_root).resolve()
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    return rel.parent / f"{rel.name}.sgr.yaml"


def sgr_filename(java_path: str | Path) -> str:
    return f"{Path(java_path).name}.sgr.yaml"
