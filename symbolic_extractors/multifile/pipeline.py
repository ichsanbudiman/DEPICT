from __future__ import annotations

from pathlib import Path
from typing import Any

from symbolic_extractors.ast_extractor import extract_java_file
from symbolic_extractors.base_extractor import id_allocator
from symbolic_extractors.multifile.compress import compress_for_gnn
from symbolic_extractors.multifile.io_utils import save_yaml, sgr_relpath
from symbolic_extractors.multifile.resolver import resolve_project_graph
from symbolic_extractors.multifile.symbol_index import (
    build_symbol_index,
    save_symbol_index,
)


def _file_header_from_graph(graph: dict[str, Any], java_name: str) -> dict[str, Any]:
    """Lift Package/File node info into the per-file SGR header."""
    package_name = None
    imports: list[str] = []
    file_id = None
    for node in graph.get("nodes") or []:
        if node.get("type") == "Package":
            package_name = node.get("name")
        if node.get("type") == "File":
            file_id = node.get("id")
            imports = list(node.get("imports") or [])
            if node.get("name"):
                java_name = node["name"]
    return {
        "id": file_id,
        "name": java_name,
        "package": package_name,
        "imports": imports,
    }


def extract_local_sgr(
    java_path: str | Path,
    project_path: str | Path,
    ids: id_allocator | None = None,
    anonymize: bool = False,
    relative_path: str | None = None,
) -> dict[str, Any]:
    """Stage 1: extract one file's local SGR (unresolved cross-file types stay ExternalType)."""
    path = Path(java_path).resolve()
    project = Path(project_path).resolve()
    graph = extract_java_file(
        path,
        project_path=project,
        ids=ids,
        anonymize=anonymize,
    )
    header = _file_header_from_graph(graph, path.name)
    if relative_path is not None:
        header["path"] = relative_path
    else:
        try:
            header["path"] = str(path.relative_to(project))
        except ValueError:
            header["path"] = path.name
    return {
        "file": header,
        "nodes": graph.get("nodes") or [],
        "edges": graph.get("edges") or [],
    }


def process_project(
    source_root: str | Path,
    output_dir: str | Path,
    project_name: str | None = None,
    anonymize: bool = False,
    java_files: list[str | Path] | None = None,
    labels: dict[str, Any] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """
    Full multifile pipeline.

    Layout written under ``output_dir``::

        metadata.yaml
        files/*.java.sgr.yaml          # raw local SGR
        graph.yaml                     # resolved project graph
        model_graph/project.yaml       # compressed GNN graph

    Optional ``java_files`` limits extraction to a subset (subgraph mode).
    Optional ``labels`` / ``extra_metadata`` are merged into metadata.yaml.
    """
    source_root = Path(source_root).resolve()
    output_dir = Path(output_dir).resolve()
    project_name = project_name or output_dir.name

    if java_files is None:
        selected = sorted(p for p in source_root.rglob("*.java") if p.is_file())
    else:
        selected = []
        for item in java_files:
            path = Path(item)
            if not path.is_absolute():
                path = (source_root / path).resolve()
            else:
                # resolve(strict=False) keeps dangling symlink targets addressable
                path = path.resolve()
            if not path.is_file():
                print(f"[WARN] skip missing java file: {path}")
                continue
            selected.append(path)
        selected = sorted(set(selected))

    if not selected:
        raise FileNotFoundError(f"No .java files selected under {source_root}")

    files_dir = output_dir / "files"
    model_dir = output_dir / "model_graph"
    files_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    # Shared allocator → globally unique IDs across the project.
    ids = id_allocator()
    file_sgrs: list[dict[str, Any]] = []
    relative_names: list[str] = []
    written: dict[str, Path] = {}
    extract_errors: list[dict[str, str]] = []

    for java_path in selected:
        try:
            rel = str(java_path.relative_to(source_root))
        except ValueError:
            rel = java_path.name
        try:
            sgr = extract_local_sgr(
                java_path,
                project_path=source_root,
                ids=ids,
                anonymize=anonymize,
                relative_path=rel,
            )
        except (RecursionError, UnicodeDecodeError, Exception) as exc:  # noqa: BLE001
            # Keep project extract alive when tree-sitter yields cyclic/ERROR trees
            # (seen on CDO) or other per-file failures.
            msg = f"{type(exc).__name__}: {exc}"
            print(f"[WARN] skip extract {rel}: {msg}")
            extract_errors.append({"path": rel, "error": msg})
            continue
        relative_names.append(rel)
        # Preserve relative dirs so a/Box.java and b/Box.java do not collide.
        out_path = files_dir / sgr_relpath(java_path, source_root)
        save_yaml(out_path, sgr)
        file_sgrs.append(sgr)
        written[rel] = out_path

    if not file_sgrs:
        raise RuntimeError(
            f"No files extracted successfully under {source_root} "
            f"(selected={len(selected)}, errors={len(extract_errors)})"
        )

    # Stage 2: symbol index
    metadata = build_symbol_index(file_sgrs, project_name, relative_names)
    if labels:
        base_labels = dict(metadata.get("labels") or {})
        base_labels.update(labels)
        metadata["labels"] = base_labels
    if extra_metadata:
        for key, value in extra_metadata.items():
            if key in ("project", "files", "symbols", "labels"):
                continue  # protect core schema keys
            metadata[key] = value
    if extract_errors:
        metadata.setdefault("extract_errors", extract_errors)
        metadata["n_extract_errors"] = len(extract_errors)
    metadata_path = output_dir / "metadata.yaml"
    save_symbol_index(metadata_path, metadata)
    written["metadata"] = metadata_path

    # Stage 3: cross-file resolution
    resolved = resolve_project_graph(file_sgrs, metadata)
    graph_path = output_dir / "graph.yaml"
    save_yaml(graph_path, resolved)
    written["graph"] = graph_path

    # Stage 4: GNN-compressed view
    model = compress_for_gnn(resolved)
    model_path = model_dir / "project.yaml"
    save_yaml(model_path, model)
    written["model_graph"] = model_path

    return written


def process_project_from_sources(
    java_sources: dict[str, str],
    output_dir: str | Path,
    project_name: str = "demo-app",
    anonymize: bool = False,
) -> dict[str, Path]:
    """
    Convenience for tests: ``{relative_path: source_text}`` without a real tree.
    Writes a temporary source mirror under ``output_dir/src``.
    """
    output_dir = Path(output_dir).resolve()
    src_root = output_dir / "src"
    for rel, text in java_sources.items():
        path = src_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return process_project(
        src_root,
        output_dir,
        project_name=project_name,
        anonymize=anonymize,
    )
