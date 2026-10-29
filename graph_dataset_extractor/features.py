"""Identifier-free per-node CSVs (docs/semantic_features.md).
  python3 features.py --project JUnit_v3.7
  python3 features.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from annotation_io import iter_instances, pruned_view
from audit_spines import project_yaml_path
from constants import (
    FIELD_FEAT_NAMES,
    KIND_FIELD,
    KIND_METHOD,
    KIND_TYPE,
    METHOD_FEAT_NAMES,
    PRIMITIVE_SIMPLE,
    TYPE_FEAT_NAMES,
)
from keepset import ancestor_types, created_pairs_with_uses, infer_element_types, method_use_types
from paths import DATASET, GRAPH_FILE, SGR_ROOT
from sgr_load import ProjectGraph, SgrNode, load_project_yaml
CLASS_KIND = {"Class": 0.0, "Interface": 1.0, "Enum": 2.0, "Record": 3.0}


def fbool(v: bool) -> float:
    return 1.0 if v else 0.0


def node_id_safe(nid: str) -> str:
    s = nid.replace(":", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")
    if len(s) > 180:
        import hashlib

        s = s[:140] + "_" + hashlib.sha1(nid.encode()).hexdigest()[:10]
    return s


def mod(n: Optional[SgrNode], name: str) -> bool:
    return bool(n and n.modifiers.get(name))


def struct(gjson: dict, nid: str) -> Tuple[float, float, float]:
    row = (gjson.get("structural_features") or {}).get(nid) or {}
    return (
        float(row.get("is_root", 0.0)),
        float(row.get("hop_distance", -1.0)),
        float(row.get("has_bidirectional_call", 0.0)),
    )


def graph_index(gjson: dict) -> Tuple[Set[str], Set[str], Dict[str, Set[str]]]:
    types, methods = set(), set()
    rel: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for n in gjson["nodes"]:
        if n["kind"] == KIND_TYPE:
            types.add(n["id"])
        elif n["kind"] == KIND_METHOD:
            methods.add(n["id"])
    for e in gjson["edges"]:
        for r in e["relation_type"]:
            rel[(e["src_id"], e["dst_id"])].add(r)
    return types, methods, rel


def rels_from(rel, src, name) -> List[str]:
    return [d for (s, d), rs in rel.items() if s == src and name in rs]


def json_owner(mid: str, types: Set[str]) -> Optional[str]:
    best = None
    tail = mid.split(":", 1)[-1]
    for t in types:
        tn = t.split(":", 1)[-1]
        if tail.startswith(tn + ".") or tail.startswith(tn + "#"):
            if best is None or len(tn) > len(best.split(":", 1)[-1]):
                best = t
    return best


def type_feats(g: ProjectGraph, tid: str, gjson: dict, rel, methods: Set[str]) -> Dict[str, float]:
    n = g.node(tid)
    fields = g.fields_of(tid)
    meths = g.methods_of(tid)
    ctors = [m for m in meths if (g.node(m) and g.node(m).type == "Constructor") or m.startswith("CT:") or m.startswith("K:")]
    n_pub = sum(1 for m in meths if mod(g.node(m), "public"))
    n_static_f = sum(1 for f in fields if mod(g.node(f), "static"))
    priv_ctor = any(mod(g.node(c), "private") for c in ctors)
    super_n = sum(1 for e in g.out.get(tid, ()) if e.type in {"EXTENDS", "IMPLEMENTS"} and g.is_type(e.target))
    sub_n = sum(1 for e in g.inn.get(tid, ()) if e.type in {"EXTENDS", "IMPLEMENTS"} and g.is_type(e.source))
    types_here = {tid}
    fan_out = 0
    fan_in = 0
    for (s, d), rs in rel.items():
        if "calls" not in rs and "delegates" not in rs:
            continue
        so = (g.owner_type(s) or json_owner(s, types_here)) == tid
        do_ = (g.owner_type(d) or json_owner(d, types_here)) == tid
        if s in methods and d in methods:
            if so and not do_:
                fan_out += 1
            if do_ and not so:
                fan_in += 1
    kind = CLASS_KIND.get(n.type if n else "", -1.0)
    is_root, hop, bidir = struct(gjson, tid)
    return {
        "class_kind": kind,
        "is_abstract": fbool(mod(n, "abstract") or (n and n.type == "Interface")),
        "is_final": fbool(mod(n, "final")),
        "is_public": fbool(mod(n, "public")),
        "n_fields_total": float(len(fields)),
        "n_fields_static": float(n_static_f),
        "n_methods_total": float(len(meths)),
        "n_methods_public": float(n_pub),
        "n_ctors_total": float(len(ctors)),
        "has_private_ctor": fbool(priv_ctor),
        "n_direct_supertypes": float(super_n),
        "n_direct_subtypes": float(sub_n),
        "fan_in_calls": float(fan_in),
        "fan_out_calls": float(fan_out),
        "is_root": is_root,
        "hop_distance": hop,
        "has_bidirectional_call": bidir,
    }


def _return_kind(g: ProjectGraph, mid: str, owner: Optional[str]) -> Dict[str, float]:
    n = g.node(mid)
    rt = (n.return_type if n else "") or ""
    used = method_use_types(g, mid)
    void = rt.lower() == "void" or not used and rt.lower() in {"void", ""}
    self = super_ = iface = other = False
    if owner:
        anc = ancestor_types(g, owner)
        ifaces = {
            e.target
            for e in g.out.get(owner, ())
            if e.type == "IMPLEMENTS" and g.is_type(e.target)
        }
        for t in used:
            if t == owner:
                self = True
            elif t in anc:
                super_ = True
            elif t in ifaces:
                iface = True
            else:
                other = True
        if not used and rt and rt.lower() not in PRIMITIVE_SIMPLE | {"void"}:
            other = True
    elif used:
        other = True
    if void and not (self or super_ or iface or other):
        pass
    elif self or super_ or iface or other:
        void = False
    return {
        "return_kind_void": fbool(void),
        "return_kind_self": fbool(self),
        "return_kind_super": fbool(super_),
        "return_kind_interface": fbool(iface),
        "return_kind_other": fbool(other),
    }


def method_feats(
    g: ProjectGraph,
    mid: str,
    gjson: dict,
    rel,
    types: Set[str],
    methods: Set[str],
) -> Dict[str, float]:
    n = g.node(mid)
    owner = g.owner_type(mid)
    is_ctor = bool(n and n.type == "Constructor") or mid.startswith(("CT:", "K:"))
    n_params = float(len(g.params.get(mid, ())))
    vis_pub, vis_pro, vis_pri = mod(n, "public"), mod(n, "protected"), mod(n, "private")
    call_dests = set(rels_from(rel, mid, "calls")) | set(rels_from(rel, mid, "delegates"))
    total_calls = float(len(call_dests))
    delegate_ms = set(rels_from(rel, mid, "delegates"))
    via_field = set(rels_from(rel, mid, "delegates_via_field"))
    fwd = min(1.0, (len(delegate_ms) / total_calls) if total_calls else 0.0)
    adaptee = min(1.0, (1.0 if via_field and total_calls else 0.0))
    creates = rels_from(rel, mid, "creates")
    used = method_use_types(g, mid)
    allocates_self = bool(owner and owner in creates)
    sub_of_ret = False
    if owner:
        for u in creates:
            if u in types and any(t in types and t != u for t in used):
                if created_pairs_with_uses(g, mid, u):
                    sub_of_ret = True
    overrides = bool(rels_from(rel, mid, "overrides") or rels_from(rel, mid, "implements_method"))
    factory_sib = False
    if owner:
        for d in call_dests:
            if g.owner_type(d) != owner:
                continue
            for e in g.out.get(d, ()):
                if e.type == "CREATES" and g.is_type(e.target) and created_pairs_with_uses(g, d, e.target):
                    factory_sib = True
    loop = False
    if owner:
        elem_types: Set[str] = set()
        for fid in g.fields_of(owner):
            if g.is_collection_field(fid) or infer_element_types(g, fid):
                elem_types |= infer_element_types(g, fid)
        for d in call_dests:
            do = json_owner(d, types) or g.owner_type(d)
            if do and do in elem_types:
                loop = True
    dispatch = False
    ref_ts = set(rels_from(rel, mid, "references"))
    if owner and ref_ts:
        for d in call_dests:
            callee_refs = set(rels_from(rel, d, "references"))
            if owner in callee_refs or any(t in callee_refs for t in ancestor_types(g, owner)):
                dispatch = True
    builder = False
    if owner:
        for d in call_dests:
            if owner in method_use_types(g, d) or owner in rels_from(rel, d, "uses"):
                builder = True
    is_root, hop, bidir = struct(gjson, mid)
    out = {
        "method_visibility_public": fbool(vis_pub),
        "method_visibility_protected": fbool(vis_pro),
        "method_visibility_private": fbool(vis_pri),
        "is_abstract": fbool(mod(n, "abstract")),
        "is_constructor": fbool(is_ctor),
        "n_params": n_params,
        "overrides_base_method": fbool(overrides),
        "calls_sibling_that_creates": fbool(factory_sib),
        "forwarding_ratio": float(fwd),
        "calls_on_held_field": float(adaptee),
        "total_calls": total_calls,
        "allocates_types_count": float(len(creates)),
        "allocates_self_type": fbool(allocates_self),
        "allocates_subtype_of_return": fbool(sub_of_ret),
        "loops_over_collection_and_calls_iface": fbool(loop),
        "synchronization_used": fbool(mod(n, "synchronized")),
        "has_dispatch_to_caller": fbool(dispatch),
        "calls_method_using_owner": fbool(builder),
        "is_root": is_root,
        "hop_distance": hop,
        "has_bidirectional_call": bidir,
    }
    out.update(_return_kind(g, mid, owner))
    return out


def field_feats(g: ProjectGraph, fid: str, gjson: dict, rel, types: Set[str]) -> Dict[str, float]:
    n = g.node(fid)
    owner = g.owner_type(fid)
    vis_pub, vis_pri, vis_pro = mod(n, "public"), mod(n, "private"), mod(n, "protected")
    pkg = not (vis_pub or vis_pri or vis_pro)
    ht = g.has_type.get(fid)
    dt = (n.datatype if n else "") or ""
    prim = dt.lower() in PRIMITIVE_SIMPLE or (
        ht and g.node(ht) is not None and g.node(ht).type == "PrimitiveType"
    )
    elems = infer_element_types(g, fid)
    has_el = bool(elems & types)
    self = super_ = iface = unrelated = False
    collab = None
    if ht and g.is_type(ht):
        collab = ht
    elif elems:
        collab = next(iter(elems))
    if owner and collab and g.is_type(collab):
        anc = ancestor_types(g, owner)
        ifaces = {
            e.target
            for e in g.out.get(owner, ())
            if e.type == "IMPLEMENTS" and g.is_type(e.target)
        }
        if collab == owner:
            self = True
        elif collab in anc:
            super_ = True
        elif collab in ifaces:
            iface = True
        else:
            unrelated = True
    wrote = any(e.type == "WRITES_FIELD" and e.target == fid for e in g.edges)
    init_new = False
    if owner:
        for ctor in g.methods_of(owner):
            cn = g.node(ctor)
            if not (cn and cn.type == "Constructor" or ctor.startswith(("CT:", "K:"))):
                continue
            creates = {e.target for e in g.out.get(ctor, ()) if e.type == "CREATES"}
            writes = any(e.type == "WRITES_FIELD" and e.target == fid for e in g.out.get(ctor, ()))
            if writes and ht and ht in creates:
                init_new = True
    is_root, hop, bidir = struct(gjson, fid)
    return {
        "field_visibility_public": fbool(vis_pub),
        "field_visibility_private": fbool(vis_pri),
        "field_visibility_protected": fbool(vis_pro),
        "field_visibility_package": fbool(pkg),
        "field_is_static": fbool(mod(n, "static")),
        "field_is_final": fbool(mod(n, "final")),
        "field_is_volatile": fbool(mod(n, "volatile")),
        "field_is_transient": fbool(mod(n, "transient")),
        "field_type_primitive": fbool(prim),
        "field_type_generic": fbool(n.generic if n else False),
        "type_relation_self": fbool(self),
        "type_relation_super": fbool(super_),
        "type_relation_interface_of_owner": fbool(iface),
        "type_relation_unrelated": fbool(unrelated),
        "field_initialized": fbool(wrote or init_new),
        "field_initialized_literal": 0.0,
        "field_initialized_new": fbool(init_new),
        "is_collection": fbool(g.is_collection_field(fid)),
        "has_project_element_type": fbool(has_el),
        "is_root": is_root,
        "hop_distance": hop,
        "has_bidirectional_call": bidir,
    }


def write_row(path: Path, names: List[str], row: Dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=names)
        w.writeheader()
        w.writerow({k: f"{row.get(k, 0.0):.6g}" for k in names})


def extract_graph(g: ProjectGraph, gjson: dict, feat_dir: Path) -> int:
    types, methods, rel = graph_index(gjson)
    index_rows = []
    n_written = 0
    for node in gjson["nodes"]:
        nid, kind = node["id"], node["kind"]
        if kind == KIND_TYPE:
            names, row = TYPE_FEAT_NAMES, type_feats(g, nid, gjson, rel, methods)
        elif kind == KIND_METHOD:
            names, row = METHOD_FEAT_NAMES, method_feats(g, nid, gjson, rel, types, methods)
        elif kind == KIND_FIELD:
            names, row = FIELD_FEAT_NAMES, field_feats(g, nid, gjson, rel, types)
        else:
            continue
        rel_csv = f"{node_id_safe(nid)}.csv"
        write_row(feat_dir / rel_csv, names, row)
        index_rows.append({"node_id": nid, "kind": kind, "csv_path": rel_csv})
        n_written += 1
    with (feat_dir / "index.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["node_id", "kind", "csv_path"])
        w.writeheader()
        w.writerows(index_rows)
    return n_written


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="v5 identifier-free node feature CSVs")
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--graph-dpd", "--sgr-root", dest="graph_dpd", type=Path, default=SGR_ROOT)
    p.add_argument("--project", default=None)
    p.add_argument("--include-inactive", action="store_true")
    args = p.parse_args(argv)

    by_folder: Dict[str, List[Tuple[Path, dict]]] = defaultdict(list)
    for path, folder, inst in iter_instances(args.dataset, args.project):
        by_folder[folder].append((path, inst))
    if not by_folder:
        print(f"no {GRAPH_FILE}", file=sys.stderr)
        return 1

    graphs = nodes = 0
    for folder, items in sorted(by_folder.items()):
        active_items = []
        for path, inst in items:
            active = (inst.get("gold") or {}).get("active", True)
            if args.include_inactive or active:
                active_items.append((path, inst))
        if not active_items:
            continue
        yp = project_yaml_path(args.graph_dpd, folder)
        print(f"load {yp}  graphs={len(active_items)}", file=sys.stderr)
        if not yp.is_file():
            print(f"missing project.yaml: {folder}", file=sys.stderr)
            continue
        sgr = load_project_yaml(yp)
        proj_index = []
        for path, inst in active_items:
            gjson = pruned_view(inst)
            stem = str(inst.get("id") or "")
            feat_dir = path.parent / "features" / stem
            n = extract_graph(sgr, gjson, feat_dir)
            nodes += n
            graphs += 1
            proj_index.append({"graph": stem, "n_nodes": n, "label": gjson.get("label", "")})
            print(f"  {stem}  nodes={n} label={gjson.get('label')}")
        idxp = active_items[0][0].parent / "features" / "index.csv"
        idxp.parent.mkdir(parents=True, exist_ok=True)
        with idxp.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["graph", "n_nodes", "label"])
            w.writeheader()
            w.writerows(proj_index)
        del sgr
    print(f"graphs={graphs} node_csvs={nodes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
