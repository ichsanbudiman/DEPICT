"""Read depict_annotation_v1 documents as one graph per instance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, List, Optional, Tuple


def catalogue_lines(gold_csv: Path, project_name: str, file_path: str) -> List[int]:
    """Gold-CSV line numbers for one project file. Line 1 is the header."""
    import csv

    if not project_name or not file_path or not gold_csv.is_file():
        return []
    lines: List[int] = []
    with gold_csv.open(newline="") as handle:
        for i, row in enumerate(csv.DictReader(handle), start=2):
            if row.get("project_name") == project_name and row.get("file_path") == file_path:
                lines.append(i)
    return lines


def instance_label(inst: dict) -> str:
    proposed = inst.get("proposed") or []
    if proposed:
        return str(proposed[0])
    gold = inst.get("gold") or {}
    if gold.get("label"):
        return str(gold["label"])
    return "None"


def out_of_model_view(node: dict) -> bool:
    if node.get("in_model_view") is False:
        return True
    return bool(node.get("is_pruned"))


def instance_core(inst: dict) -> str:
    root = (inst.get("graph") or {}).get("root") or ""
    return root.split(":", 1)[-1].replace(".", "_").replace("/", "_")


def listed_stem_matches(inst: dict, stem: str) -> bool:
    """Match a legacy subgraph_<core>_<label> name to an annotation instance."""
    core = instance_core(inst)
    return stem == f"subgraph_{core}_{instance_label(inst)}" or stem == f"subgraph_{core}_Adapter"


def pruned_view(inst: dict) -> dict:
    """Graph with out-of-view nodes and edges removed."""
    graph = inst.get("graph") or {}
    drop = {node["id"] for node in graph.get("nodes") or [] if out_of_model_view(node)}
    nodes = [node for node in graph.get("nodes") or [] if node["id"] not in drop]
    edges = [
        edge
        for edge in graph.get("edges") or []
        if edge.get("in_model_view") is not False
        and edge["src_id"] not in drop
        and edge["dst_id"] not in drop
    ]
    feats = {
        nid: row
        for nid, row in (graph.get("structural_features") or {}).items()
        if nid not in drop
    }
    return {
        "root": graph.get("root"),
        "nodes": nodes,
        "edges": edges,
        "structural_features": feats,
        "label": instance_label(inst),
        "gold": dict(inst.get("gold") or {}),
    }


def annotation_paths(dataset: Path, project: Optional[str] = None) -> List[Path]:
    files = sorted(dataset.glob("*/annotation.json"))
    if project:
        files = [path for path in files if path.parent.name == project]
    return files


def iter_instances(
    dataset: Path, project: Optional[str] = None
) -> Iterator[Tuple[Path, str, dict]]:
    for path in annotation_paths(dataset, project):
        doc = json.loads(path.read_text())
        folder = path.parent.name
        for inst in doc.get("instances") or []:
            yield path, folder, inst


def load_document(path: Path) -> dict:
    return json.loads(path.read_text())


def write_document(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2) + "\n")


def find_instance(doc: dict, stem: str) -> dict:
    hits = [inst for inst in doc.get("instances") or [] if listed_stem_matches(inst, stem)]
    if len(hits) != 1:
        raise SystemExit(f"expected one instance for {stem}, found {len(hits)}")
    return hits[0]
