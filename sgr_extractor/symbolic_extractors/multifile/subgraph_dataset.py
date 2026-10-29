"""DPD sample corpus → project-level (default) or per-subgraph SGR artifacts.

Expected on-disk layout (from ``sample_java_projects``)::

    <project>/
      javas/{class_id}.java
      subgraph/subgraph_{root}.json   # optional catalog / old per-subgraph mode
      nodes.csv
      labels.csv

Default (project mode) writes one sample per project::

    <output>/<project>/
      metadata.yaml
      files/javas/*.java.sgr.yaml
      full_project_graph.yaml
      project_sgr.yaml
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from symbolic_extractors.multifile.io_utils import save_yaml
from symbolic_extractors.multifile.pipeline import process_project


@dataclass(frozen=True)
class SubgraphSpec:
    """One labeled class set from a DPD project."""

    project_dir: Path
    root: str
    class_ids: tuple[str, ...]
    label: str
    json_path: Path

    @property
    def subgraph_id(self) -> str:
        return f"subgraph_{self.root}"


def _safe_name(name: str) -> str:
    """Filesystem-safe project folder name."""
    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip())
    return cleaned.strip("_") or "project"


def load_nodes_csv(project_dir: str | Path) -> dict[str, dict[str, str]]:
    """Map ``class_id`` → row from ``nodes.csv``."""
    path = Path(project_dir) / "nodes.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Missing nodes.csv in {project_dir}")
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        cid = (row.get("class_id") or "").strip()
        if cid:
            out[cid] = row
    return out


def load_subgraph_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Subgraph JSON must be an object: {path}")
    if "root" not in data or "nodes" not in data:
        raise ValueError(f"Subgraph JSON missing root/nodes: {path}")
    return data


def iter_subgraph_specs(project_dir: str | Path) -> Iterator[SubgraphSpec]:
    """Yield every subgraph under ``project_dir/subgraph/``."""
    project_dir = Path(project_dir).resolve()
    subgraph_dir = project_dir / "subgraph"
    if not subgraph_dir.is_dir():
        raise FileNotFoundError(f"Missing subgraph/ directory in {project_dir}")

    for json_path in sorted(subgraph_dir.glob("subgraph_*.json")):
        data = load_subgraph_json(json_path)
        root = str(data["root"])
        nodes = tuple(str(n) for n in (data.get("nodes") or []))
        label = str(data.get("label") or "Unknown")
        yield SubgraphSpec(
            project_dir=project_dir,
            root=root,
            class_ids=nodes,
            label=label,
            json_path=json_path,
        )


def resolve_subgraph_java_files(
    spec: SubgraphSpec,
    nodes_index: dict[str, dict[str, str]] | None = None,
) -> list[Path]:
    """
    Map subgraph class IDs to existing ``javas/{class_id}.java`` files.

    Skips missing files (recorded by caller via return length vs class_ids).
    """
    project_dir = spec.project_dir
    javas_dir = project_dir / "javas"
    nodes_index = nodes_index if nodes_index is not None else load_nodes_csv(project_dir)

    files: list[Path] = []
    seen: set[Path] = set()
    for class_id in spec.class_ids:
        candidates = [
            javas_dir / f"{class_id}.java",
        ]
        row = nodes_index.get(class_id) or {}
        java_path = (row.get("java_path") or "").strip()
        if java_path:
            # java_path may be like ./dpd_graph_v3/Project/javas/id.java
            # Prefer local javas/ by class_id; fall back to basename under javas/.
            candidates.append(javas_dir / Path(java_path).name)

        chosen = next((p for p in candidates if p.is_file()), None)
        if chosen is None:
            continue
        chosen = chosen.resolve()
        if chosen not in seen:
            seen.add(chosen)
            files.append(chosen)
    return sorted(files)


def process_subgraph(
    project_dir: str | Path,
    subgraph: str | Path | SubgraphSpec,
    output_dir: str | Path,
    *,
    anonymize: bool = False,
    skip_missing: bool = True,
) -> dict[str, Path]:
    """
    Extract one subgraph into ``output_dir``.

    ``subgraph`` may be a root id (``86fa8325``), a path to JSON, or a ``SubgraphSpec``.
    """
    project_dir = Path(project_dir).resolve()
    output_dir = Path(output_dir).resolve()

    if isinstance(subgraph, SubgraphSpec):
        spec = subgraph
    else:
        spec = _coerce_subgraph_spec(project_dir, subgraph)

    nodes_index = load_nodes_csv(project_dir)
    java_files = resolve_subgraph_java_files(spec, nodes_index)
    if not java_files:
        raise FileNotFoundError(
            f"No Java files found for {spec.subgraph_id} in {project_dir / 'javas'}"
        )
    if not skip_missing and len(java_files) != len(spec.class_ids):
        missing = sorted(set(spec.class_ids) - {p.stem for p in java_files})
        raise FileNotFoundError(
            f"{spec.subgraph_id}: missing Java for class_ids {missing}"
        )

    labels = {
        "pattern": spec.label,
        "violation": None,
        "commit": None,
        "roles": {},
        "subgraph_root": spec.root,
        "subgraph_id": spec.subgraph_id,
    }
    extra = {
        "subgraph": {
            "id": spec.subgraph_id,
            "root": spec.root,
            "label": spec.label,
            "class_ids": list(spec.class_ids),
            "java_files_found": [p.name for p in java_files],
            "java_files_missing": sorted(
                set(spec.class_ids) - {p.stem for p in java_files}
            ),
            "source_project": project_dir.name,
            "source_json": str(spec.json_path),
        }
    }

    return process_project(
        source_root=project_dir,
        output_dir=output_dir,
        project_name=f"{_safe_name(project_dir.name)}::{spec.subgraph_id}",
        anonymize=anonymize,
        java_files=java_files,
        labels=labels,
        extra_metadata=extra,
    )


def _coerce_subgraph_spec(project_dir: Path, subgraph: str | Path) -> SubgraphSpec:
    path = Path(subgraph)
    if path.suffix == ".json" and path.is_file():
        json_path = path.resolve()
    else:
        root = str(subgraph)
        if root.startswith("subgraph_"):
            root = root[len("subgraph_"):]
        json_path = project_dir / "subgraph" / f"subgraph_{root}.json"
        if not json_path.is_file():
            raise FileNotFoundError(f"Subgraph JSON not found: {json_path}")

    data = load_subgraph_json(json_path)
    return SubgraphSpec(
        project_dir=project_dir,
        root=str(data["root"]),
        class_ids=tuple(str(n) for n in (data.get("nodes") or [])),
        label=str(data.get("label") or "Unknown"),
        json_path=json_path,
    )


def load_labels_csv(project_dir: str | Path) -> dict[str, str]:
    """Map ``class_id`` → pattern label from ``labels.csv`` (if present)."""
    path = Path(project_dir) / "labels.csv"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out: dict[str, str] = {}
    for row in rows:
        cid = (row.get("class_id") or "").strip()
        label = (row.get("label") or "").strip()
        if cid and label:
            out[cid] = label
    return out


def catalog_subgraphs(project_dir: str | Path) -> list[dict[str, Any]]:
    """Lightweight subgraph catalog for project metadata (not extracted separately)."""
    catalog: list[dict[str, Any]] = []
    try:
        specs = list(iter_subgraph_specs(project_dir))
    except FileNotFoundError:
        return catalog
    for spec in specs:
        catalog.append({
            "id": spec.subgraph_id,
            "root": spec.root,
            "label": spec.label,
            "n_classes": len(spec.class_ids),
            "class_ids": list(spec.class_ids),
        })
    return catalog


def _collect_java_sources(project_dir: Path) -> tuple[list[Path], str]:
    """Return (java files, source_mode).

    Prefers DPD flat ``javas/*.java``; otherwise recursively finds ``*.java``
    under a normal project tree (skips build/VCS dirs).
    """
    javas_dir = project_dir / "javas"
    if javas_dir.is_dir():
        files = sorted(p for p in javas_dir.glob("*.java") if p.is_file())
        if files:
            return files, "javas"

    skip_parts = {
        "target", "build", "out", "bin", ".git", ".svn", ".hg",
        "node_modules", ".idea", ".gradle", "cmake-build-debug",
        "cmake-build-release", "test-classes",
    }
    files: list[Path] = []
    for path in sorted(project_dir.rglob("*.java")):
        if any(part in skip_parts for part in path.parts):
            continue
        # Skip broken symlinks / missing paths (e.g. platform .../templ.java)
        if not path.is_file():
            continue
        files.append(path)
    if not files:
        raise FileNotFoundError(
            f"No .java files under {project_dir} "
            f"(expected javas/*.java or a normal source tree)"
        )
    return files, "tree"


def process_dpd_project(
    project_dir: str | Path,
    output_root: str | Path,
    *,
    anonymize: bool = False,
) -> dict[str, Path]:
    """
    Extract one project as a single multifile SGR.

    Accepts either:

    * DPD layout: ``<project>/javas/*.java``
    * Normal Java tree: ``**/*.java`` under ``<project>/`` (e.g. ``src/``)

    Layout written::

        <output_root>/<safe_project_name>/
          metadata.yaml
          files/.../*.java.sgr.yaml
          full_project_graph.yaml
          project_sgr.yaml

    Optional DPD ``labels.csv`` / ``subgraph/`` enrich metadata only.
    """
    project_dir = Path(project_dir).resolve()
    output_root = Path(output_root).resolve()
    java_files, source_mode = _collect_java_sources(project_dir)

    project_out = output_root / _safe_name(project_dir.name)
    class_labels = load_labels_csv(project_dir)
    subgraph_catalog = catalog_subgraphs(project_dir)

    labels = {
        "pattern": None,  # project-level; patterns live on classes/subgraphs
        "violation": None,
        "commit": None,
        "roles": {},
        "class_labels": class_labels,
    }
    extra = {
        "dpd": {
            "source_project": project_dir.name,
            "source": str(project_dir),
            "source_mode": source_mode,
            "n_java_files": len(java_files),
            "n_class_labels": len(class_labels),
            "n_subgraphs": len(subgraph_catalog),
            "subgraphs": subgraph_catalog,
        }
    }

    return process_project(
        source_root=project_dir,
        output_dir=project_out,
        project_name=project_dir.name,
        anonymize=anonymize,
        java_files=java_files,
        labels=labels,
        extra_metadata=extra,
    )


def process_dpd_project_subgraphs(
    project_dir: str | Path,
    output_root: str | Path,
    *,
    anonymize: bool = False,
    limit: int | None = None,
    subgraph_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Optional: extract each subgraph separately under ``subgraphs/``.

    Prefer ``process_dpd_project`` for project-level graphs.
    """
    project_dir = Path(project_dir).resolve()
    output_root = Path(output_root).resolve()
    project_out = output_root / _safe_name(project_dir.name)
    subgraphs_out = project_out / "subgraphs"
    subgraphs_out.mkdir(parents=True, exist_ok=True)

    wanted = None
    if subgraph_ids:
        wanted = set()
        for item in subgraph_ids:
            s = item.strip()
            if s.startswith("subgraph_"):
                s = s[len("subgraph_"):]
            wanted.add(s)

    results: dict[str, Any] = {
        "project": project_dir.name,
        "output": str(project_out),
        "subgraphs": {},
        "errors": {},
    }

    count = 0
    for spec in iter_subgraph_specs(project_dir):
        if wanted is not None and spec.root not in wanted:
            continue
        if limit is not None and count >= limit:
            break
        out_dir = subgraphs_out / spec.subgraph_id
        try:
            written = process_subgraph(
                project_dir,
                spec,
                out_dir,
                anonymize=anonymize,
            )
            results["subgraphs"][spec.subgraph_id] = {
                "label": spec.label,
                "paths": {k: str(v) for k, v in written.items()},
            }
        except Exception as exc:  # noqa: BLE001 — collect per-subgraph failures
            results["errors"][spec.subgraph_id] = f"{type(exc).__name__}: {exc}"
        count += 1

    index = rebuild_project_index(project_out, source=project_dir, run_errors=results["errors"])
    results["subgraphs"] = {
        sid: {"label": info["label"]}
        for sid, info in (index.get("subgraphs") or {}).items()
    }
    results["errors"] = index.get("errors") or {}
    return results


def rebuild_project_index(
    project_out: str | Path,
    *,
    source: str | Path | None = None,
    run_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Scan ``subgraphs/*/metadata.yaml`` and rewrite ``index.yaml`` to match disk."""
    from symbolic_extractors.multifile.io_utils import load_yaml

    project_out = Path(project_out).resolve()
    subgraphs_out = project_out / "subgraphs"
    listed: dict[str, dict[str, str]] = {}
    for meta_path in sorted(subgraphs_out.glob("subgraph_*/metadata.yaml")):
        try:
            meta = load_yaml(meta_path)
        except Exception:
            continue
        sid = meta_path.parent.name
        label = (
            (meta.get("labels") or {}).get("pattern")
            or (meta.get("subgraph") or {}).get("label")
            or "Unknown"
        )
        listed[sid] = {"label": str(label)}

    index = {
        "project": Path(source).name if source else project_out.name,
        "source": str(Path(source).resolve()) if source else None,
        "n_subgraphs_ok": len(listed),
        "n_subgraphs_failed": len(run_errors or {}),
        "subgraphs": listed,
        "errors": run_errors or {},
    }
    save_yaml(project_out / "index.yaml", index)
    return index


def process_dpd_corpus(
    corpus_root: str | Path,
    output_root: str | Path,
    *,
    anonymize: bool = False,
    project_limit: int | None = None,
) -> dict[str, Any]:
    """Process every project folder under a DPD corpus root (project-level graphs)."""
    corpus_root = Path(corpus_root).resolve()
    output_root = Path(output_root).resolve()
    projects = sorted(
        p for p in corpus_root.iterdir()
        if p.is_dir() and (p / "javas").is_dir()
    )
    if project_limit is not None:
        projects = projects[:project_limit]

    summary: dict[str, Any] = {"corpus": str(corpus_root), "projects": {}}
    for project_dir in projects:
        try:
            written = process_dpd_project(
                project_dir,
                output_root,
                anonymize=anonymize,
            )
            summary["projects"][project_dir.name] = {
                "ok": True,
                "output": str(Path(output_root) / _safe_name(project_dir.name)),
                "graph": str(written["graph"]),
                "model_graph": str(written["model_graph"]),
            }
        except Exception as exc:  # noqa: BLE001
            summary["projects"][project_dir.name] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    save_yaml(output_root / "corpus_index.yaml", {
        "corpus": str(corpus_root),
        "mode": "project",
        "n_projects": len(summary["projects"]),
        "n_ok": sum(1 for p in summary["projects"].values() if p.get("ok")),
        "projects": {
            name: {"ok": info.get("ok"), "error": info.get("error")}
            for name, info in summary["projects"].items()
        },
    })
    return summary


def _print_project_result(written: dict[str, Path], project_name: str) -> None:
    print(f"project: {project_name}")
    for key in ("metadata", "graph", "model_graph"):
        if key in written:
            print(f"  {key}: {written[key]}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract project-level SGR graphs from DPD sample_java_projects",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Path to one project folder (javas/ or normal Java tree)",
    )
    parser.add_argument(
        "--output",
        default="dataset/dpd_sgr",
        help="Output root (default: dataset/dpd_sgr)",
    )
    parser.add_argument(
        "--mode",
        choices=("project", "subgraphs"),
        default="project",
        help="project = one graph for all javas (default); subgraphs = per-subgraph graphs",
    )
    parser.add_argument(
        "--subgraph",
        action="append",
        default=None,
        help="Only with --mode subgraphs: subgraph root id (repeatable).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only with --mode subgraphs: max subgraphs",
    )
    parser.add_argument("--anonymize", action="store_true")
    args = parser.parse_args()

    if args.mode == "subgraphs":
        result = process_dpd_project_subgraphs(
            args.project,
            args.output,
            anonymize=args.anonymize,
            limit=args.limit,
            subgraph_ids=args.subgraph,
        )
        print(f"project: {result.get('project')}")
        print(f"  ok={len(result.get('subgraphs') or {})} failed={len(result.get('errors') or {})}")
        print(f"  output={result.get('output')}")
    else:
        written = process_dpd_project(
            args.project,
            args.output,
            anonymize=args.anonymize,
        )
        _print_project_result(written, Path(args.project).name)
