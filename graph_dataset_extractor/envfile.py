"""Load ``.env`` from the repo root.

That file is not committed. Each machine keeps its own copy. A variable already
set in the process environment is left unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

DEPICT_ROOT = Path(__file__).resolve().parent.parent


def depict_env_path() -> Path:
    return DEPICT_ROOT / ".env"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise SystemExit(f"{path}: expected KEY=VALUE, got {raw!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not key:
            raise SystemExit(f"{path}: empty name in {raw!r}")
        values[key] = value
    return values


def load_depict_env() -> Path:
    """Read ``.env`` into ``os.environ``. Return the file path."""
    path = depict_env_path()
    if not path.is_file():
        raise SystemExit(f"missing {path}")
    for key, value in parse_env_file(path).items():
        os.environ.setdefault(key, value)
    return path
