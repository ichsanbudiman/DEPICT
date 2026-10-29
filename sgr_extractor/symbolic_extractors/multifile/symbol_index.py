from __future__ import annotations

from pathlib import Path
from typing import Any

from symbolic_extractors.multifile.io_utils import load_yaml, save_yaml

TYPE_KINDS = frozenset({"Class", "Interface", "Enum", "Record"})
METHOD_KINDS = frozenset({"Method", "Constructor"})


def build_symbol_index(
    file_sgrs: list[dict[str, Any]],
    project_name: str,
    source_files: list[str],
) -> dict[str, Any]:
    """Create metadata.yaml content from per-file SGR graphs."""
    classes: dict[str, dict[str, str]] = {}
    methods: dict[str, dict[str, str]] = {}
    fields: dict[str, dict[str, str]] = {}

    for sgr in file_sgrs:
        file_meta = sgr.get("file") or {}
        # Prefer project-relative path to avoid basename collisions.
        file_name = file_meta.get("path") or file_meta.get("name") or "unknown.java"
        for node in sgr.get("nodes") or []:
            qn = node.get("qualified_name")
            if not qn:
                continue
            nid = node["id"]
            ntype = node.get("type")
            if ntype in TYPE_KINDS:
                classes[qn] = {
                    "file": file_name,
                    "id": nid,
                    "kind": ntype,
                }
            elif ntype in METHOD_KINDS:
                # Skip unresolved external stubs (e.g. this.strategy.execute).
                if node.get("external"):
                    continue
                methods[qn] = {
                    "file": file_name,
                    "id": nid,
                    "kind": ntype,
                }
            elif ntype == "Field":
                fields[qn] = {
                    "file": file_name,
                    "id": nid,
                    "kind": ntype,
                }

    return {
        "project": {"name": project_name},
        "files": source_files,
        "labels": {
            "pattern": None,       
            "violation": None,     
            "commit": None,        
            "roles": {},           
        },
        "symbols": {
            "classes": classes,
            "methods": methods,
            "fields": fields,
        },
    }


def load_symbol_index(metadata_path: str | Path) -> dict[str, Any]:
    return load_yaml(metadata_path)


def save_symbol_index(metadata_path: str | Path, index: dict[str, Any]) -> None:
    save_yaml(metadata_path, index)


def lookup_type(
    index: dict[str, Any],
    simple_or_qn: str,
    *,
    package: str | None = None,
    imports: list[str] | None = None,
) -> dict[str, str] | None:
    """Resolve a type name using FQN, same-package, or imports."""
    classes = (index.get("symbols") or {}).get("classes") or {}
    if simple_or_qn in classes:
        return classes[simple_or_qn]

    # Same package
    if package and "." not in simple_or_qn:
        cand = f"{package}.{simple_or_qn}"
        if cand in classes:
            return classes[cand]

    imports = imports or []
    for imp in imports:
        if imp.endswith(f".{simple_or_qn}"):
            if imp in classes:
                return classes[imp]
        if imp.endswith(".*"):
            pkg = imp[:-2]
            cand = f"{pkg}.{simple_or_qn}"
            if cand in classes:
                return classes[cand]

    # Simple-name fallback: unique match among indexed classes
    matches = [
        entry for qn, entry in classes.items()
        if qn.rsplit(".", 1)[-1] == simple_or_qn
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def lookup_method(
    index: dict[str, Any],
    simple_or_qn: str,
    *,
    owner_qn: str | None = None,
) -> dict[str, str] | None:
    methods = (index.get("symbols") or {}).get("methods") or {}
    if simple_or_qn in methods:
        return methods[simple_or_qn]
    if owner_qn and "." not in simple_or_qn:
        cand = f"{owner_qn}.{simple_or_qn}"
        if cand in methods:
            return methods[cand]
    matches = [
        entry for qn, entry in methods.items()
        if qn.rsplit(".", 1)[-1] == simple_or_qn
    ]
    if len(matches) == 1:
        return matches[0]
    return None
