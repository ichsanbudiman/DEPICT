from __future__ import annotations

from copy import deepcopy
from typing import Any

from symbolic_extractors.multifile.compress import (
    BEHAVIOR_OWNERS,
    COUNT_EDGE_TYPES,
    _build_parent_index,
    _order_from_ancestors,
    _reanchor_behavior_source,
)
from symbolic_extractors.multifile.symbol_index import lookup_method, lookup_type

TYPE_KINDS = frozenset({"Class", "Interface", "Enum", "Record"})
KEEP_ALWAYS = TYPE_KINDS | {
    "Package",
    "File",
    "Field",
    "Parameter",
    "Variable",
    "PrimitiveType",
    "Constructor",
}

# Expression/statement sources also summarized onto enclosing Method/Constructor
# so project graph.yaml answers method-level queries (sgr-testbed checker).
LIFT_TO_METHOD_EDGES = frozenset({
    "CALLS",
    "CREATES",
    "READS_FIELD",
    "WRITES_FIELD",
    "ACCESSES_FIELD",
})


def resolve_project_graph(
    file_sgrs: list[dict[str, Any]],
    symbol_index: dict[str, Any],
) -> dict[str, Any]:
    """Merge per-file SGRs and rewrite unresolved type/method edges."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for sgr in file_sgrs:
        for node in sgr.get("nodes") or []:
            nid = node["id"]
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            nodes.append(deepcopy(node))
        edges.extend(deepcopy(e) for e in (sgr.get("edges") or []))

    node_by_id = {n["id"]: n for n in nodes}
    qn_to_method_id = {
        n["qualified_name"]: n["id"]
        for n in nodes
        if n.get("type") == "Method"
        and not n.get("external")
        and n.get("qualified_name")
    }

    file_ctx: dict[str, tuple[str | None, list[str]]] = {}
    node_file: dict[str, str] = {}
    for sgr in file_sgrs:
        meta = sgr.get("file") or {}
        fid = meta.get("id")
        if not fid:
            continue
        file_ctx[fid] = (meta.get("package"), list(meta.get("imports") or []))
        for node in sgr.get("nodes") or []:
            node_file[node["id"]] = fid

    def ctx_for(node_id: str) -> tuple[str | None, list[str]]:
        fid = node_file.get(node_id)
        if fid and fid in file_ctx:
            return file_ctx[fid]
        return None, []

    # --- Resolve ExternalType → project type ids ---
    external_redirect: dict[str, str] = {}
    for node in nodes:
        if node.get("type") != "ExternalType":
            continue
        simple = node.get("name") or (node.get("qualified_name") or "").rsplit(".", 1)[-1]
        package, imports = None, []
        for e in edges:
            if e.get("target") == node["id"]:
                package, imports = ctx_for(e["source"])
                if package or imports:
                    break
        hit = lookup_type(
            symbol_index,
            node.get("qualified_name") or simple,
            package=package,
            imports=imports,
        )
        if hit is None and simple:
            hit = lookup_type(symbol_index, simple, package=package, imports=imports)
        if hit and hit["id"] in node_by_id and hit["id"] != node["id"]:
            external_redirect[node["id"]] = hit["id"]

    # Apply type redirects to edges first so CALLS resolution sees real types.
    for edge in edges:
        if edge.get("target") in external_redirect:
            edge["target"] = external_redirect[edge["target"]]
        if edge.get("source") in external_redirect:
            edge["source"] = external_redirect[edge["source"]]

    # --- Resolve external Method stubs (CALLS) ---
    method_redirect: dict[str, str] = {}
    type_methods = _type_method_index(nodes, edges, node_by_id)

    for node in nodes:
        if not (node.get("type") == "Method" and node.get("external")):
            continue
        qn = node.get("qualified_name")
        name = node.get("name")
        resolved: str | None = None

        if qn and qn in qn_to_method_id:
            resolved = qn_to_method_id[qn]
        elif name:
            owner_id = _owner_type_for_external_call(node["id"], edges, node_by_id)
            if owner_id and name in type_methods.get(owner_id, {}):
                resolved = type_methods[owner_id][name]
            else:
                hit = lookup_method(
                    symbol_index,
                    name,
                    owner_qn=_owner_qn(owner_id, node_by_id),
                )
                if hit and hit["id"] in node_by_id and not _is_external(node_by_id, hit["id"]):
                    resolved = hit["id"]

        if resolved and resolved != node["id"]:
            method_redirect[node["id"]] = resolved

    redirect = {**external_redirect, **method_redirect}

    # Merge duplicate Package / PrimitiveType nodes created per-file.
    canonical_redirect = _merge_canonical_nodes(nodes)
    redirect = {**redirect, **canonical_redirect}

    # Apply all remaining redirects to edges.
    new_edges: list[dict[str, Any]] = []
    seen_edge: dict[tuple[str, str, str], dict[str, Any]] = {}
    count_types = frozenset({
        "CALLS", "CREATES", "READS_FIELD", "WRITES_FIELD", "ACCESSES_FIELD",
    })
    for edge in edges:
        e = deepcopy(edge)
        for endpoint in ("source", "target"):
            seen_hop: set[str] = set()
            while e.get(endpoint) in redirect and e[endpoint] not in seen_hop:
                seen_hop.add(e[endpoint])
                e[endpoint] = redirect[e[endpoint]]
        if e["source"] == e["target"]:
            continue
        key = (e["source"], e["target"], e["type"])
        if key in seen_edge:
            if e["type"] in count_types:
                prev = seen_edge[key]
                prev["count"] = int(prev.get("count") or 1) + int(e.get("count") or 1)
            continue
        if "count" not in e:
            e["count"] = 1
        seen_edge[key] = e
        new_edges.append(e)

    used_ids = {e["source"] for e in new_edges} | {e["target"] for e in new_edges}
    new_nodes: list[dict[str, Any]] = []
    for node in nodes:
        nid = node["id"]
        if nid in redirect:
            continue
        ntype = node.get("type")
        is_ext_type = ntype == "ExternalType"
        is_ext_method = ntype == "Method" and node.get("external")

        if is_ext_type and nid not in used_ids:
            continue
        if is_ext_method and nid not in used_ids:
            continue

        if nid in used_ids or ntype in KEEP_ALWAYS or not (is_ext_type or is_ext_method):
            out = deepcopy(node) if is_ext_type else node
            if is_ext_type:
                out["resolved"] = False
                out["unresolved"] = True
            new_nodes.append(out)

    # Safety: never leave dangling edge endpoints.
    node_ids = {n["id"] for n in new_nodes}
    new_edges = [
        e for e in new_edges
        if e["source"] in node_ids and e["target"] in node_ids
    ]

    # Recompute project-level IMPLEMENTS_METHOD / OVERRIDES
    new_edges = [
        e for e in new_edges
        if e["type"] not in ("IMPLEMENTS_METHOD", "OVERRIDES")
    ]
    new_edges.extend(_compute_override_edges(new_nodes, new_edges))
    new_edges = _lift_behavior_edges_to_methods(new_nodes, new_edges)
    _recompute_pattern_features(new_nodes, new_edges)

    return {
        "project": symbol_index.get("project"),
        "labels": symbol_index.get("labels") or {
            "pattern": None,
            "violation": None,
            "commit": None,
            "roles": {},
        },
        "nodes": new_nodes,
        "edges": new_edges,
        "resolution": {
            "external_types_resolved": len(external_redirect),
            "external_methods_resolved": len(method_redirect),
            "canonical_nodes_merged": len(canonical_redirect),
        },
    }


def _lift_behavior_edges_to_methods(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy expression-level behavior onto enclosing Method/Constructor.

    Keeps the original fine-grained edges and adds aggregated method-level
    CALLS / CREATES / READS_FIELD / WRITES_FIELD (with ``count``).
    """
    node_by_id = {n["id"]: n for n in nodes}
    parent = _build_parent_index(edges)
    stmt_order = {
        n["id"]: n.get("order")
        for n in nodes
        if n.get("order") is not None
    }
    keep_ids = {
        nid for nid, n in node_by_id.items()
        if n.get("type") in BEHAVIOR_OWNERS
    }

    index: dict[tuple, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for e in edges:
        key = (e["source"], e["target"], e["type"])
        if e["type"] == "DELEGATES_VIA":
            key = (e["source"], e["target"], e["type"], e.get("via"), e.get("via_kind"))
        index[key] = e
        out.append(e)

    for e in list(edges):
        etype = e.get("type")
        if etype not in LIFT_TO_METHOD_EDGES:
            continue
        src = e.get("source")
        tgt = e.get("target")
        if src is None or tgt is None:
            continue
        src_node = node_by_id.get(src) or {}
        if src_node.get("type") in BEHAVIOR_OWNERS:
            continue
        owner = _reanchor_behavior_source(src, parent, node_by_id, keep_ids)
        if owner is None or owner == src:
            continue
        if owner == tgt:
            continue

        key = (owner, tgt, etype)
        if key in index:
            if etype in COUNT_EDGE_TYPES:
                prev = index[key]
                prev["count"] = int(prev.get("count") or 1) + int(e.get("count") or 1)
            continue

        order = e.get("order")
        if order is None:
            order = _order_from_ancestors(src, parent, stmt_order)
        lifted: dict[str, Any] = {
            "source": owner,
            "target": tgt,
            "type": etype,
            "count": int(e.get("count") or 1),
        }
        if order is not None:
            lifted["order"] = order
        if etype == "WRITES_FIELD" and e.get("provenance") is not None:
            lifted["provenance"] = e["provenance"]
        index[key] = lifted
        out.append(lifted)

    return out


def _recompute_pattern_features(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """Refresh Adapter/Decorator/Facade cues after cross-file resolution."""
    node_by_id = {n["id"]: n for n in nodes}
    methods_of: dict[str, list[str]] = {}
    field_type: dict[str, str] = {}
    implements_of: dict[str, list[str]] = {}

    for e in edges:
        if e["type"] == "HAS_METHOD":
            methods_of.setdefault(e["source"], []).append(e["target"])
        elif e["type"] == "HAS_TYPE":
            src = node_by_id.get(e["source"]) or {}
            if src.get("type") == "Field":
                field_type[e["source"]] = e["target"]
        elif e["type"] == "IMPLEMENTS":
            implements_of.setdefault(e["source"], []).append(e["target"])

    delegates = [e for e in edges if e["type"] == "DELEGATES_VIA"]
    for node in nodes:
        if node.get("type") not in TYPE_KINDS:
            continue
        mids = set(methods_of.get(node["id"], []))
        class_dels = [e for e in delegates if e["source"] in mids]
        targets = {e["target"] for e in class_dels}
        fields = {
            e["via"] for e in class_dels
            if e.get("via_kind") == "field" and e.get("via")
        }
        params = {
            e["via"] for e in class_dels
            if e.get("via_kind") == "parameter" and e.get("via")
        }
        same: bool | None = None
        implemented = set(implements_of.get(node["id"], []))
        for fid in fields:
            ft = field_type.get(fid)
            if ft is None:
                continue
            if ft in implemented or ft == node["id"]:
                same = True
                break
            if same is None:
                same = False
        node["pattern_features"] = {
            "distinct_delegate_targets": len(targets),
            "distinct_delegate_fields": len(fields),
            "distinct_delegate_params": len(params),
            "delegates_same_interface": same,
        }


def _merge_canonical_nodes(nodes: list[dict[str, Any]]) -> dict[str, str]:
    """Collapse duplicate Package / PrimitiveType nodes onto one canonical id."""
    redirect: dict[str, str] = {}

    packages: dict[str, str] = {}
    primitives: dict[str, str] = {}
    for node in nodes:
        ntype = node.get("type")
        nid = node["id"]
        if ntype == "Package":
            key = node.get("name") or ""
            if key in packages:
                redirect[nid] = packages[key]
            else:
                packages[key] = nid
        elif ntype == "PrimitiveType":
            key = node.get("name") or ""
            if key in primitives:
                redirect[nid] = primitives[key]
            else:
                primitives[key] = nid
    return redirect


def _is_external(node_by_id: dict[str, dict], nid: str) -> bool:
    n = node_by_id.get(nid) or {}
    return bool(n.get("external") or n.get("type") == "ExternalType")


def _owner_qn(owner_id: str | None, node_by_id: dict[str, dict]) -> str | None:
    if not owner_id:
        return None
    n = node_by_id.get(owner_id) or {}
    return n.get("qualified_name")


def _type_method_index(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict],
) -> dict[str, dict[str, str]]:
    type_methods: dict[str, dict[str, str]] = {
        n["id"]: {} for n in nodes if n.get("type") in TYPE_KINDS
    }
    for e in edges:
        if e["type"] != "HAS_METHOD":
            continue
        owner, mid = e["source"], e["target"]
        m = node_by_id.get(mid)
        if m and m.get("name") and owner in type_methods and not m.get("external"):
            type_methods[owner][m["name"]] = mid
    return type_methods


def _owner_type_for_external_call(
    em_id: str,
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict],
) -> str | None:
    """Follow CALLS←invocation→RECEIVER→(ACCESSES_FIELD|REFERS_TO)→HAS_TYPE."""
    call_ids = [e["source"] for e in edges if e["type"] == "CALLS" and e["target"] == em_id]
    for call_id in call_ids:
        receivers = [
            e["target"] for e in edges
            if e["source"] == call_id and e["type"] == "RECEIVER"
        ]
        for recv in receivers:
            owner = _type_of_receiver(recv, edges, node_by_id)
            if owner:
                return owner
    return None


def _type_of_receiver(
    recv_id: str,
    edges: list[dict[str, Any]],
    node_by_id: dict[str, dict],
) -> str | None:
    recv = node_by_id.get(recv_id) or {}

    for e in edges:
        if e["source"] == recv_id and e["type"] in (
            "ACCESSES_FIELD", "READS_FIELD", "WRITES_FIELD",
        ):
            return _has_type_target(e["target"], edges)
        if e["source"] == recv_id and e["type"] == "REFERS_TO":
            return _has_type_target(e["target"], edges)

    if recv.get("type") == "FieldAccess":
        for e in edges:
            if e["source"] == recv_id and e["type"] in (
                "ACCESSES_FIELD", "READS_FIELD", "WRITES_FIELD",
            ):
                return _has_type_target(e["target"], edges)

    # Identifier / name already a typed symbol
    if recv.get("type") in ("Field", "Parameter", "Variable"):
        return _has_type_target(recv_id, edges)

    return None


def _has_type_target(symbol_id: str, edges: list[dict[str, Any]]) -> str | None:
    for e in edges:
        if e["source"] == symbol_id and e["type"] == "HAS_TYPE":
            return e["target"]
    return None


def _compute_override_edges(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    type_methods: dict[str, dict[str, str]] = {}
    extends_of: dict[str, str] = {}
    implements_of: dict[str, list[str]] = {}

    node_by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        if n.get("type") in TYPE_KINDS:
            type_methods[n["id"]] = {}

    for e in edges:
        if e["type"] == "HAS_METHOD":
            owner, mid = e["source"], e["target"]
            m = node_by_id.get(mid)
            if m and m.get("name") and owner in type_methods and not m.get("external"):
                type_methods[owner][m["name"]] = mid
        elif e["type"] == "EXTENDS":
            extends_of[e["source"]] = e["target"]
        elif e["type"] == "IMPLEMENTS":
            implements_of.setdefault(e["source"], []).append(e["target"])

    out: list[dict[str, Any]] = []
    for type_id, methods in type_methods.items():
        for iface_id in implements_of.get(type_id, []):
            for name, iface_mid in type_methods.get(iface_id, {}).items():
                if name in methods:
                    out.append({
                        "source": methods[name],
                        "target": iface_mid,
                        "type": "IMPLEMENTS_METHOD",
                    })
        parent = extends_of.get(type_id)
        seen: set[str] = set()
        while parent and parent not in seen:
            seen.add(parent)
            for name, parent_mid in type_methods.get(parent, {}).items():
                if name in methods and methods[name] != parent_mid:
                    out.append({
                        "source": methods[name],
                        "target": parent_mid,
                        "type": "OVERRIDES",
                    })
            parent = extends_of.get(parent)
    return out
