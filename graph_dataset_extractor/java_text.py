"""Fill snippet windows from the original Java trees.

metadata.yaml stores a project-relative path on each class, method, and field.
The file text is not in the SGR. When ``JAVA_PROJECTS`` points at the checkouts,
those paths are opened and stored as the same windows the SGR extractor writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from sgr_load import ProjectGraph


def file_by_id(meta: dict) -> Dict[str, str]:
    """Node id → path stored under metadata symbols."""
    out: Dict[str, str] = {}
    symbols = meta.get("symbols") or {}
    if not isinstance(symbols, dict):
        return out
    for section in ("classes", "methods", "fields"):
        entries = symbols.get(section) or {}
        if not isinstance(entries, dict):
            continue
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            nid = entry.get("id")
            rel = entry.get("file")
            if nid and rel:
                out[str(nid)] = str(rel).replace("\\", "/").lstrip("/")
    return out


def display_path(project_name: str, rel: str) -> str:
    """Template path: ``{project}/{path inside the project}``."""
    rel = rel.replace("\\", "/").lstrip("/")
    prefix = project_name.rstrip("/") + "/"
    if project_name and rel.startswith(prefix):
        return rel
    if project_name:
        return f"{project_name}/{rel}"
    return rel


def stamp_node_files(g: ProjectGraph, meta: dict, project_name: str) -> Dict[str, str]:
    """Set ``node.file`` from metadata when the SGR node has none.

    Returns display path → raw metadata path, for the files that were stamped.
    """
    by_id = file_by_id(meta)
    display_to_raw: Dict[str, str] = {}
    for node in g.nodes.values():
        if node.file or node.external:
            continue
        rel = by_id.get(node.id)
        if not rel:
            continue
        shown = display_path(project_name, rel)
        node.file = shown
        display_to_raw.setdefault(shown, rel)
    return display_to_raw


def java_file_candidates(
    java_root: Path,
    folder: str,
    project_name: str,
    rel: str,
) -> list[Path]:
    """Places a metadata path might sit under the Java checkout root."""
    rel = rel.replace("\\", "/").lstrip("/")
    roots: list[str] = []
    for name in (folder, project_name):
        if name and name not in roots:
            roots.append(name)
    tails = [rel]
    for name in roots:
        prefix = name + "/"
        if rel.startswith(prefix):
            tails.append(rel[len(prefix) :])
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    for tail in tails:
        for name in roots:
            add(java_root / name / tail)
        add(java_root / tail)
    return found


def read_java_source(path: Path) -> str:
    """Same encoding fallbacks as the SGR extractor."""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def source_window(text: str) -> dict:
    """Full-file window. ``to`` counts a trailing newline."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return {"from": 1, "to": 1, "text": ""}
    return {"from": 1, "to": text.count("\n") + 1, "text": text}


def locate_java(
    java_root: Path,
    folder: str,
    project_name: str,
    rel: str,
) -> Optional[Path]:
    for path in java_file_candidates(java_root, folder, project_name, rel):
        if path.is_file():
            return path
    return None


def load_windows(
    display_paths,
    display_to_raw: Dict[str, str],
    java_root: Path,
    folder: str,
    project_name: str,
) -> tuple[dict, int]:
    """Read each used file once. Missing files are counted and skipped."""
    windows: dict = {}
    missing = 0
    cache: Dict[str, Optional[dict]] = {}
    for shown in sorted(set(display_paths)):
        raw = display_to_raw.get(shown)
        if not raw:
            # Already a display path, or a path stamped by the SGR extractor.
            raw = shown
            prefix = project_name.rstrip("/") + "/"
            if project_name and raw.startswith(prefix):
                raw = raw[len(prefix) :]
        if raw not in cache:
            path = locate_java(java_root, folder, project_name, raw)
            if path is None:
                cache[raw] = None
            else:
                window = source_window(read_java_source(path))
                window["path"] = shown
                window["relative"] = raw
                cache[raw] = window
        window = cache[raw]
        if window is None:
            missing += 1
            continue
        stored = dict(window)
        stored["path"] = shown
        windows[shown] = stored
    return windows, missing
