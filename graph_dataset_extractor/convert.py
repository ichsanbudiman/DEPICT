"""Example usage:

  python3 convert.py \\
    --sgr-root ../../dataset \\
    --out ../../dataset \\
    --project JUnit_v3.7
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from constants import (
    ANNOTATION_SCHEMA,
    FIELD_SGR,
    KIND_FIELD,
    KIND_METHOD,
    KIND_TYPE,
    TYPE_SGR,
)
from keepset import (
    NEIGHBOURHOOD_V1,
    NEIGHBOURHOODS,
    keep_set,
    parse_neighbourhood,
    assert_type_hops,
    project_call_targets,
    structural_features,
    v5_edges,
)
from paths import GOLD_CSV, GRAPH_FILE, MEMBER_OUT, SGR_ROOT
from sgr_load import (
    ProjectGraph,
    SgrNode,
    load_metadata_document,
    load_metadata_types,
    load_project_model,
)


def folder_name(project_name: str) -> str:
    return project_name.replace(" ", "_")


def rel_from_gold(file_path: str, project_name: str) -> str:
    prefix = project_name.rstrip("/") + "/"
    if file_path.startswith(prefix):
        return file_path[len(prefix) :]
    return file_path


PATH_REMAP_FOLDERS = frozenset(
    {
        "ChenSun",
        "JEdit_v4.2_(From_Source)",
        "JEdit_v4.2_From_Source",
        "biomixer",
        "patra",
        "dto_config_models",
        "dto_io_models",
        "dto_metrics_models",
        "dto_paging_models",
        "dto_profile_models",
        "dto_spatial_models",
    }
)


def resolve_type_id(
    rel: str, by_file: Dict[str, str], folder: str
) -> Optional[Tuple[str, str]]:
    rel = rel.replace("\\", "/")
    if rel in by_file:
        return by_file[rel], rel
    hits = []
    for f, tid in by_file.items():
        f = f.replace("\\", "/")
        if f.endswith("/" + rel) or rel.endswith("/" + f):
            hits.append((f, tid))
    if len({t for _, t in hits}) == 1:
        return hits[0][1], hits[0][0]
    if folder not in PATH_REMAP_FOLDERS:
        return None
    parts = [p for p in rel.split("/") if p]
    if len(parts) < 2:
        return None
    for n in range(min(len(parts), 8), 1, -1):
        tail = "/".join(parts[-n:])
        found = [tid for f, tid in by_file.items() if f.endswith("/" + tail) or f == tail]
        tids = set(found)
        if len(tids) == 1:
            meta = next(f for f, tid in by_file.items() if tid == next(iter(tids)) and (f.endswith("/" + tail) or f == tail))
            return next(iter(tids)), meta
    return None


def json_stem(root_id: str, label: str) -> str:
    core = root_id.split(":", 1)[-1].replace(".", "_").replace("/", "_")
    return f"subgraph_{core}_{label}"


def load_gold_groups(
    gold_csv: Path,
    graph_dpd: Path,
    active_only: bool,
    project_filter: Optional[str],
) -> Dict[str, List[dict]]:
    """folder → list of unique {root_id, label, file_path, project_name, csv_lines, ...}."""
    by_folder: Dict[str, List[dict]] = defaultdict(list)
    grouped: Dict[Tuple[str, str, str], dict] = {}

    with gold_csv.open(newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            pn = row["project_name"]
            folder = folder_name(pn)
            if project_filter and folder != project_filter and pn != project_filter:
                continue
            proj_dir = graph_dpd / folder
            if not proj_dir.is_dir():
                continue
            active = str(row.get("active", "")).strip() in {"1", "true", "True"}
            if active_only and not active:
                continue
            rel = rel_from_gold(row["file_path"], pn)
            key = (folder, rel, row["label"])
            if key not in grouped:
                grouped[key] = {
                    "folder": folder,
                    "project_name": pn,
                    "file_path": row["file_path"],
                    "rel": rel,
                    "label": row["label"],
                    "csv_lines": [],
                    "active_csv_lines": [],
                    "any_active": False,
                }
            grouped[key]["csv_lines"].append(i)
            if active:
                grouped[key]["active_csv_lines"].append(i)
                grouped[key]["any_active"] = True

    meta_cache: Dict[str, Dict[str, str]] = {}
    for rec in grouped.values():
        folder = rec["folder"]
        if folder not in meta_cache:
            meta_path = graph_dpd / folder / "metadata.yaml"
            if not meta_path.is_file():
                raise FileNotFoundError(f"missing metadata.yaml: {meta_path}")
            meta_cache[folder] = load_metadata_types(meta_path)
        resolved = resolve_type_id(rec["rel"], meta_cache[folder], folder)
        if not resolved:
            print(
                f"skip no type id: {rec['project_name']} {rec['rel']}",
                file=sys.stderr,
            )
            continue
        root_id, meta_file = resolved
        if meta_file != rec["rel"]:
            print(
                f"path remap: {rec['project_name']} {rec['rel']} -> {meta_file}",
                file=sys.stderr,
            )
        rec["root_id"] = root_id
        rec["active"] = bool(rec["active_csv_lines"]) if rec["any_active"] else False
        by_folder[folder].append(rec)
    return by_folder


_TYPE_FACT_KIND = {
    "Class": "class",
    "Interface": "interface",
    "Enum": "enum",
    "Record": "record",
}


def _visibility(mods: Dict[str, bool]) -> str:
    if mods.get("public"):
        return "public"
    if mods.get("protected"):
        return "protected"
    if mods.get("private"):
        return "private"
    return "package"


_RAW_COLLECTION = {
    "List", "ArrayList", "LinkedList", "Set", "HashSet", "TreeSet", "LinkedHashSet",
    "Collection", "Queue", "Deque", "Map", "HashMap", "TreeMap", "Hashtable",
    "Vector", "Stack",
}
_MUTATE_CALL = re.compile(
    r"\b([A-Za-z_]\w*)\s*\.\s*(add|addElement|push|offer|put)\s*\("
)
_RETURN_DECL = re.compile(
    r"\b([A-Z][\w.]*(?:<[^<>]*>)?(?:\[\])*)\s+([A-Za-z_]\w*)\s*\("
)


def _simple_type(text: str) -> str:
    base = text.strip()
    if "<" in base:
        base = base.split("<", 1)[0]
    return base.split(".")[-1].strip()


def _generic_args(datatype: str) -> List[str]:
    if "<" not in datatype or ">" not in datatype:
        return []
    inner = datatype[datatype.find("<") + 1 : datatype.rfind(">")]
    args: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in inner:
        if ch == "<":
            depth += 1
            buf.append(ch)
        elif ch == ">":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf and "".join(buf).strip():
        args.append("".join(buf).strip())
    return [a for a in args if a]


def _split_params(text: str) -> List[str]:
    parts: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in text:
        if ch in "<([":
            depth += 1
            buf.append(ch)
        elif ch in ">)]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _cstyle_signature(signature: str, source: str) -> str:
    """Move ``Type name[]`` brackets onto the type: ``Type[] name``."""
    if not signature or not source or "[]" not in source:
        return signature
    name = signature.split("(", 1)[0].strip()
    header = source.split("{", 1)[0]
    match = re.search(rf"\b{re.escape(name)}\s*\((.*)\)", header, re.S)
    if not match:
        return signature
    fixed: List[str] = []
    changed = False
    for param in _split_params(match.group(1)):
        param = param.strip()
        moved = re.match(r"^(.*\S)\s+([A-Za-z_]\w*)\s*((?:\[\s*\])+)$", param)
        if moved:
            brackets = re.sub(r"\s+", "", moved.group(3))
            fixed.append(f"{moved.group(1)}{brackets} {moved.group(2)}")
            changed = True
        elif param:
            fixed.append(param)
    if not changed:
        return signature
    return f"{name}({', '.join(fixed)})"


def _local_types(signature: str, source: str) -> Dict[str, str]:
    found: Dict[str, str] = {}
    if "(" in signature and ")" in signature:
        inner = signature[signature.find("(") + 1 : signature.rfind(")")]
        for param in _split_params(inner):
            bits = param.strip().split()
            if len(bits) >= 2:
                found[bits[-1]] = bits[-2]
    for match in re.finditer(
        r"\b([A-Z][\w.]*(?:<[^;>]*>)?(?:\[\])*)\s+([A-Za-z_]\w*)\s*[=;]",
        source or "",
    ):
        found.setdefault(match.group(2), _simple_type(match.group(1)))
    return found


def _call_args(source: str, start: int) -> Optional[str]:
    """Text inside the parentheses that open at ``start``."""
    depth = 1
    i = start
    while i < len(source) and depth:
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
        i += 1
    if depth:
        return None
    return source[start : i - 1]


def _expr_type(expr: str, locals_: Dict[str, str], sources: List[str], g: ProjectGraph) -> Optional[str]:
    """Type of an add/put argument: a local, a constructor, or a call's return type."""
    expr = expr.strip()
    call = re.search(r"\.([A-Za-z_]\w*)\s*\(", expr)
    if call:
        name = call.group(1)
        receiver = expr.split(".", 1)[0].strip()
        receiver_type = locals_.get(receiver)
        if receiver_type:
            for cand in g.methods_named(name):
                owner = g.owner_type(cand)
                if g.simple_name(owner or "") != receiver_type:
                    continue
                ret = (g.node(cand).return_type if g.node(cand) else "") or ""
                if ret and ret != "void":
                    return _simple_type(ret)
        for source in sources:
            for match in _RETURN_DECL.finditer(source):
                if match.group(2) == name:
                    return _simple_type(match.group(1))
        return None
    ident = re.match(r"[A-Za-z_]\w*$", expr)
    if ident and ident.group(0) in locals_:
        return _simple_type(locals_[ident.group(0)])
    created = re.match(r"new\s+([A-Z][\w.]*)", expr)
    if created:
        return _simple_type(created.group(1))
    return None


def _derived_element(g: ProjectGraph, node: SgrNode, fid: str) -> Optional[str]:
    texts: List[str] = []
    locals_: Dict[str, str] = {}
    owner = g.owner_type(fid)
    if owner:
        owner_node = g.node(owner)
        if owner_node and owner_node.source:
            texts.append(owner_node.source)
        for mid in g.methods_of(owner):
            method = g.node(mid)
            if method is None:
                continue
            locals_.update(_local_types(method.signature, ""))
            if method.source:
                texts.append(method.source)
    for mid in list(g.field_delegators.get(fid, ())) + list(g.field_writers.get(fid, ())):
        method = g.node(mid)
        if method is None:
            continue
        locals_.update(_local_types(method.signature, method.source))
        if method.source:
            texts.append(method.source)
    names: List[str] = []
    seen = set()
    for source in texts:
        if source in seen:
            continue
        seen.add(source)
        locals_.update(_local_types("", source))
        for call in _MUTATE_CALL.finditer(source):
            if call.group(1) != node.name:
                continue
            args = _call_args(source, call.end())
            if args is None:
                continue
            parts = [part.strip() for part in _split_params(args) if part.strip()]
            if call.group(2) == "put":
                if len(parts) < 2:
                    continue
                chosen = parts[1]
            else:
                chosen = parts[0] if parts else ""
            kind = _expr_type(chosen, locals_, texts, g)
            if kind:
                names.append(kind)
    if not names:
        return None
    return sorted(set(names), key=lambda item: (-names.count(item), item))[0]


def _declared_datatype(node: SgrNode) -> str:
    """Put C-style ``name[]`` brackets onto the type."""
    datatype = node.datatype or ""
    source = node.source or ""
    if not node.name or "[]" not in source or "[]" in datatype:
        return datatype
    match = re.search(rf"\b{re.escape(node.name)}\s*((?:\[\s*\])+)", source)
    if not match:
        return datatype
    return datatype + re.sub(r"\s+", "", match.group(1))


def _field_element(g: ProjectGraph, node: SgrNode, fid: str, datatype: str) -> tuple:
    """Return (element_type or None, derived)."""
    if "[]" in datatype:
        elem = datatype[: datatype.rfind("[]")].strip()
        return (_simple_type(elem) if elem else None), False
    args = _generic_args(datatype)
    if args:
        return _simple_type(args[-1]), False
    for edge in g.out.get(fid, ()):
        if edge.type == "HAS_ELEMENT_TYPE":
            return g.simple_name(edge.target), False
    if node.is_collection or _simple_type(datatype) in _RAW_COLLECTION:
        return _derived_element(g, node, fid), True
    return None, False


def _project_supertype(g: ProjectGraph, name: str) -> bool:
    """True when ``name`` is a type in the project, not a library type."""
    return any(g.is_type(tid) for tid in g.types_by_simple.get(name, ()))


def _align_supertype_facts(g: ProjectGraph, nodes: List[dict]) -> None:
    for node in nodes:
        facts = node.get("facts") or {}
        if node.get("kind") != KIND_TYPE or "supertypes" not in facts:
            continue
        facts["supertypes"] = [name for name in facts["supertypes"] if _project_supertype(g, name)]


def _flag(facts: dict, mods: Dict[str, bool], name: str) -> None:
    if mods.get(name):
        facts[name] = True


def _facts(g: ProjectGraph, nid: str, node: Optional[SgrNode]) -> dict:
    if node is None:
        return {}
    if node.type in TYPE_SGR:
        facts = {
            "kind": _TYPE_FACT_KIND.get(node.type, "class"),
            "abstract": bool(node.modifiers.get("abstract")),
            "package": node.package,
            "supertypes": list(node.supertypes),
            "external": bool(node.external),
        }
        _flag(facts, node.modifiers, "static")
        _flag(facts, node.modifiers, "final")
        return facts
    if node.type in FIELD_SGR:
        datatype = _declared_datatype(node)
        is_array = "[]" in datatype
        is_collection = (bool(node.is_collection) or _simple_type(datatype) in _RAW_COLLECTION) and not is_array
        facts = {
            "datatype": datatype,
            "is_collection": is_collection,
            "visibility": _visibility(node.modifiers),
        }
        if is_array:
            facts["is_array"] = True
        element, derived = _field_element(g, node, nid, datatype)
        if element:
            facts["element_type"] = element
            if derived and not is_array:
                facts["element_type_derived"] = True
        _flag(facts, node.modifiers, "final")
        _flag(facts, node.modifiers, "static")
        if node.annotations:
            facts["annotations"] = list(node.annotations)
        return facts
    facts = {
        "signature": _cstyle_signature(node.signature, node.source),
        "return_type": node.return_type or "void",
        "visibility": _visibility(node.modifiers),
    }
    _flag(facts, node.modifiers, "static")
    _flag(facts, node.modifiers, "final")
    if node.annotations:
        facts["annotations"] = list(node.annotations)
    if node.modifiers.get("abstract"):
        facts["abstract"] = True
    return facts


def _render_node(
    g: ProjectGraph,
    nid: str,
    kind: str,
    gold: str,
    hidden: set,
) -> dict:
    node = g.node(nid)
    out: dict = {"id": nid, "kind": kind}
    if node and node.file:
        out["file"] = node.file
    if node and isinstance(node.line_start, int) and isinstance(node.line_end, int):
        out["lines"] = [node.line_start, node.line_end]
    if node and node.source:
        out["source"] = node.source
    out["facts"] = _facts(g, nid, node)
    if nid in hidden:
        out["in_model_view"] = False
    if nid == gold and kind == KIND_TYPE:
        out["root"] = True
    return out


def _pointed_at_by(g: ProjectGraph, nid: str, sources: set) -> bool:
    for edge in g.inn.get(nid, ()):
        if edge.source in sources:
            return True
    for edge in g.edges:
        if edge.via == nid and edge.source in sources:
            return True
    return False


def _hop_sort_key(g: ProjectGraph, nid: str, kind: str, feats: dict) -> tuple:
    owner = nid if kind == KIND_TYPE else (g.owner_type(nid) or nid)
    hop = (feats.get(nid) or {}).get("hop_distance", -1.0)
    if not isinstance(hop, (int, float)) or hop < 0:
        hop = 10**6
    if kind == KIND_TYPE:
        role = 0
    elif kind == KIND_FIELD:
        role = 1
    elif nid.startswith("CT:"):
        role = 2
    else:
        role = 3
    return (hop, owner, role, nid)


def member_graph(
    g: ProjectGraph,
    gold: str,
    max_hops: int,
    neighbourhood: str = NEIGHBOURHOOD_V1,
) -> dict:
    neighbourhood = parse_neighbourhood(neighbourhood)
    keep_types, keep_methods, keep_fields = keep_set(
        g, gold, drop_tests=True, max_hops=max_hops, neighbourhood=neighbourhood
    )
    root_methods = set(g.methods_of(gold))
    root_fields = set(g.fields_of(gold))
    root_kept = {
        nid
        for nid in (keep_methods | keep_fields)
        if g.owner_type(nid) == gold
    }
    hidden = (root_methods | root_fields) - root_kept
    if neighbourhood == NEIGHBOURHOOD_V1:
        collab_methods = {
            nid
            for nid in keep_methods
            if g.owner_type(nid) not in {None, gold} and _pointed_at_by(g, nid, root_kept)
        }
        collab_fields = {
            nid
            for nid in keep_fields
            if g.owner_type(nid) not in {None, gold} and _pointed_at_by(g, nid, root_kept)
        }
        view_methods = (keep_methods & root_methods) | collab_methods
        view_fields = (keep_fields & root_fields) | collab_fields
    else:
        view_methods = set(keep_methods)
        view_fields = set(keep_fields)
    view_types = set(keep_types)
    # Rule 1. Only a root member's own call may bring in a type.
    root_callers = {nid for nid in view_methods if g.owner_type(nid) == gold}
    called_methods, _called_types = project_call_targets(g, root_callers)
    called_methods -= hidden
    view_methods |= called_methods
    view_types |= {g.owner_type(nid) for nid in called_methods if g.owner_type(nid)}
    model_edges = v5_edges(g, view_types, view_methods, view_fields)
    if neighbourhood == NEIGHBOURHOOD_V1:
        used = {gold}
        for edge in model_edges:
            used.add(edge["src_id"])
            used.add(edge["dst_id"])
        view_types = {tid for tid in view_types if tid in used}
        view_methods = {nid for nid in view_methods if g.owner_type(nid) in view_types}
        view_fields = {nid for nid in view_fields if g.owner_type(nid) in view_types}
        model_edges = v5_edges(g, view_types, view_methods, view_fields)
    methods = view_methods | (hidden & root_methods)
    fields = view_fields | (hidden & root_fields)
    members: List[Tuple[str, str]] = (
        [(nid, KIND_TYPE) for nid in view_types]
        + [(nid, KIND_METHOD) for nid in methods]
        + [(nid, KIND_FIELD) for nid in fields]
    )
    edges = v5_edges(g, view_types, methods, fields)
    for edge in edges:
        if edge["src_id"] in hidden or edge["dst_id"] in hidden:
            edge["in_model_view"] = False
    feats = structural_features(
        g, gold, view_types | {gold}, view_methods, view_fields, model_edges
    )
    assert_type_hops(feats, view_types, max_hops)
    for nid in hidden:
        feats[nid] = {
            "is_root": 0.0,
            "hop_distance": 0.0,
            "has_bidirectional_call": 0.0,
        }
    members.sort(key=lambda item: _hop_sort_key(g, item[0], item[1], feats))
    nodes = [_render_node(g, nid, kind, gold, hidden) for nid, kind in members]
    order = {node["id"]: i for i, node in enumerate(nodes)}
    edges.sort(
        key=lambda edge: (
            order.get(edge["src_id"], 10**9),
            order.get(edge["dst_id"], 10**9),
            edge["relation_type"],
        )
    )
    _align_supertype_facts(g, nodes)
    return {
        "root": gold,
        "nodes": nodes,
        "edges": edges,
        "structural_features": {node["id"]: feats[node["id"]] for node in nodes if node["id"] in feats},
    }


def _instance_id(project_name: str, csv_line: int) -> str:
    token = project_name.split()[0] if project_name else "project"
    slug = "".join(ch for ch in token.lower() if ch.isalnum()) or "project"
    return f"{slug}-{csv_line:04d}"


def _proposed(label: str) -> List[str]:
    if label and label != "None":
        return [label]
    return []


def _snippets(sources: dict, used_files: Iterable[str]) -> dict:
    windows = {}
    for path in sorted(used_files):
        rec = sources.get(path) or {}
        if not rec:
            continue
        windows[f"{path}#window"] = {
            "path": rec.get("path") or path,
            "from": rec.get("from", 1),
            "to": rec.get("to"),
            "text": rec.get("text") or "",
        }
    return windows


def _drop_covered_source(node: dict, sources: dict) -> None:
    rec = sources.get(node.get("file") or "") or {}
    lines = node.get("lines")
    if not rec or not lines or "source" not in node:
        return
    frm = rec.get("from", 1)
    to = rec.get("to")
    if isinstance(frm, int) and isinstance(to, int) and frm <= lines[0] and to >= lines[1]:
        node.pop("source", None)


def annotation_document(
    g: ProjectGraph,
    recs: List[dict],
    sources: dict,
    graphs: List[dict],
    max_hops: int,
    neighbourhood: str,
) -> dict:
    instances = []
    used: set = set()
    for rec, graph in zip(recs, graphs):
        root_node = g.node(rec["root_id"])
        root_name = root_node.name if root_node and root_node.name else g.simple_name(rec["root_id"])
        line = rec["csv_lines"][0] if rec.get("csv_lines") else 0
        for node in graph["nodes"]:
            if node.get("file"):
                used.add(node["file"])
            _drop_covered_source(node, sources)
        instances.append(
            {
                "id": _instance_id(rec["project_name"], line),
                "root": root_name,
                "gold": {
                    "file_path": rec["file_path"],
                    "project_name": rec["project_name"],
                },
                "graph": graph,
                "proposed": _proposed(rec["label"]),
                "hops": max_hops,
                "neighbourhood": neighbourhood,
            }
        )
    instances.sort(key=lambda item: item["id"])
    project = recs[0]["project_name"] if recs else g.project_name
    return {
        "schema": ANNOTATION_SCHEMA,
        "project": project,
        "hops": max_hops,
        "neighbourhood": neighbourhood,
        "snippets": _snippets(sources, used),
        "instances": instances,
    }


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def convert_project(
    folder: str,
    recs: List[dict],
    graph_dpd: Path,
    out_root: Path,
    max_hops: int = 1,
    neighbourhood: str = NEIGHBOURHOOD_V1,
) -> None:
    neighbourhood = parse_neighbourhood(neighbourhood)
    yaml_path = graph_dpd / folder / "sgr_project.yaml"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"missing sgr_project.yaml: {yaml_path}")
    print(f"load {yaml_path}")
    g = load_project_model(yaml_path)
    meta = load_metadata_document(graph_dpd / folder / "metadata.yaml")
    print(f"  nodes={len(g.nodes)} edges={len(g.edges)} graphs={len(recs)}")
    proj_out = out_root / folder
    for stale in ("subgraph", "subgraph_pruned"):
        stale_dir = proj_out / stale
        if stale_dir.is_dir():
            shutil.rmtree(stale_dir)
    graphs: List[dict] = []
    for rec in recs:
        stem = json_stem(rec["root_id"], rec["label"])
        graph = member_graph(g, rec["root_id"], max_hops, neighbourhood)
        graphs.append(graph)
        e = len(graph["edges"])
        hidden_n = sum(1 for node in graph["nodes"] if node.get("in_model_view") is False)
        kinds = {}
        for node in graph["nodes"]:
            kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
        print(
            f"  {stem}  types={kinds.get(KIND_TYPE, 0)} "
            f"methods={kinds.get(KIND_METHOD, 0)} fields={kinds.get(KIND_FIELD, 0)} "
            f"edges={e} out_of_view={hidden_n} hops={max_hops} "
            f"neighbourhood={neighbourhood} label={rec['label']}"
        )
    document = annotation_document(
        g, recs, meta.get("sources") or {}, graphs, max_hops, neighbourhood
    )
    ann_path = proj_out / GRAPH_FILE
    write_json(ann_path, document)
    print(
        f"  graph {ann_path}  instances={len(document['instances'])} "
        f"snippets={len(document['snippets'])}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="SGR sgr_project.yaml → graph.json"
    )
    p.add_argument(
        "--graph-dpd",
        "--sgr-root",
        dest="graph_dpd",
        type=Path,
        default=SGR_ROOT,
        help="Root of SGR project folders (each has sgr_project.yaml)",
    )
    p.add_argument(
        "--gold",
        type=Path,
        default=GOLD_CSV,
    )
    p.add_argument(
        "--out",
        type=Path,
        default=MEMBER_OUT,
    )
    p.add_argument(
        "--active-only",
        action="store_true",
        default=True,
        help="Only gold.active=1 (default on for sample convert)",
    )
    p.add_argument(
        "--include-inactive",
        action="store_true",
        help="Also convert active=0 rows",
    )
    p.add_argument(
        "--project",
        default=None,
        help="Restrict to one sample folder (e.g. JUnit_v3.7)",
    )
    p.add_argument(
        "--hops",
        type=int,
        default=1,
        help="Type-hop radius from the gold type (members share their type's hop)",
    )
    p.add_argument(
        "--neighbourhood",
        choices=NEIGHBOURHOODS,
        default=NEIGHBOURHOOD_V1,
        help="v1 is the original keep-set. v2 is the paper's collaboration neighbourhood",
    )
    args = p.parse_args(argv)
    active_only = not args.include_inactive
    if not args.gold.is_file():
        print(f"gold CSV missing: {args.gold}", file=sys.stderr)
        return 1
    if not args.graph_dpd.is_dir():
        print(f"Graph-DPD root missing: {args.graph_dpd}", file=sys.stderr)
        return 1
    groups = load_gold_groups(args.gold, args.graph_dpd, active_only, args.project)
    if not groups:
        print("no gold rows matched Graph-DPD folders", file=sys.stderr)
        return 1
    for folder, recs in sorted(groups.items()):
        try:
            convert_project(
                folder,
                recs,
                args.graph_dpd,
                args.out,
                args.hops,
                args.neighbourhood,
            )
        except Exception as exc:
            print(f"FAIL {folder}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
