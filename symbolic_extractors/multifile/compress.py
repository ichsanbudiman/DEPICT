"""Compress a resolved project SGR into a GNN-friendly model graph."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Keep structural / semantic entities for pattern detection.
KEEP_NODE_TYPES = frozenset({
    "Package",
    "File",
    "Class",
    "Interface",
    "Enum",
    "Record",
    "Method",
    "Constructor",
    "Field",
    "Parameter",
    "Variable",
    "ExternalType",
    "PrimitiveType",
})

KEEP_EDGE_TYPES = frozenset({
    "IN_PACKAGE",
    "DEFINES",
    "EXTENDS",
    "IMPLEMENTS",
    "HAS_FIELD",
    "HAS_METHOD",
    "HAS_CONSTRUCTOR",
    "HAS_PARAMETER",
    "HAS_VARIABLE",
    "HAS_TYPE",
    "HAS_ELEMENT_TYPE",
    "RETURNS",
    "RETURNS_ELEMENT",
    "CALLS",
    "CREATES",
    "READS_FIELD",
    "WRITES_FIELD",
    "REFERS_TO",
    "WRITES_LOCAL",
    "DELEGATES_VIA",
    "IMPLEMENTS_METHOD",
    "OVERRIDES",
})

# Edges whose source is often an expression/statement that we drop.
REANCHOR_SOURCE_EDGES = frozenset({
    "CALLS",
    "CREATES",
    "HAS_VARIABLE",
    "READS_FIELD",
    "WRITES_FIELD",
    "REFERS_TO",
    "WRITES_LOCAL",
})

# Multiplicity matters for Facade/Adapter delegation counts.
COUNT_EDGE_TYPES = frozenset({
    "CALLS",
    "CREATES",
    "READS_FIELD",
    "WRITES_FIELD",
    "DELEGATES_VIA",
})

PARENT_EDGE_TYPES = frozenset({
    "HAS_EXPRESSION",
    "HAS_STATEMENT",
    "THEN",
    "ELSE",
    "LHS",
    "RHS",
    "RECEIVER",
    "ARGUMENT",
    "INITIALIZED_BY",
    "HAS_VARIABLE",
    "HAS_PARAMETER",
    "DECLARED_IN",
    "NEXT_STATEMENT",
})

BEHAVIOR_OWNERS = frozenset({"Method", "Constructor"})


def compress_for_gnn(project_graph: dict[str, Any]) -> dict[str, Any]:
    all_nodes = {n["id"]: n for n in project_graph.get("nodes") or []}
    all_edges = list(project_graph.get("edges") or [])

    keep_ids = {
        nid for nid, n in all_nodes.items()
        if n.get("type") in KEEP_NODE_TYPES
    }
    parent = _build_parent_index(all_edges)
    stmt_order = {
        n["id"]: n.get("order")
        for n in all_nodes.values()
        if n.get("order") is not None
    }
    nodes = [deepcopy(all_nodes[nid]) for nid in keep_ids if nid in all_nodes]

    edges: list[dict[str, Any]] = []
    index: dict[tuple, dict[str, Any]] = {}

    def _edge_key(src: str, tgt: str, etype: str, e: dict[str, Any]):
        if etype == "DELEGATES_VIA":
            return (src, tgt, etype, e.get("via"), e.get("via_kind"))
        return (src, tgt, etype)

    def _append_edge(
        src: str,
        tgt: str,
        etype: str,
        e: dict[str, Any],
        order: Any,
        *,
        count: int,
    ) -> None:
        key = _edge_key(src, tgt, etype, e)
        if key in index:
            if etype in COUNT_EDGE_TYPES:
                index[key]["count"] = int(index[key].get("count") or 1) + count
            return
        edge = {"source": src, "target": tgt, "type": etype, "count": count}
        if order is not None and etype in COUNT_EDGE_TYPES | {"WRITES_LOCAL"}:
            edge["order"] = order
        if etype == "DELEGATES_VIA":
            if e.get("via") is not None:
                edge["via"] = e["via"]
            if e.get("via_kind") is not None:
                edge["via_kind"] = e["via_kind"]
        if etype == "WRITES_FIELD" and e.get("provenance") is not None:
            edge["provenance"] = e["provenance"]
        index[key] = edge
        edges.append(edge)

    # Pass 1: edges whose source is already a kept node (incl. method-lifted).
    for e in all_edges:
        etype = e.get("type")
        if etype not in KEEP_EDGE_TYPES:
            continue
        src, tgt = e.get("source"), e.get("target")
        if src is None or tgt is None:
            continue
        if src not in keep_ids or tgt not in keep_ids:
            continue
        if src == tgt:
            continue
        order = e.get("order")
        if order is None:
            order = _order_from_ancestors(src, parent, stmt_order)
        _append_edge(src, tgt, etype, e, order, count=int(e.get("count") or 1))

    # Pass 2: reanchor expression/statement sources onto Method/Constructor.
    # Skip keys already set in pass 1 so lifted counts are not double-counted.
    for e in all_edges:
        etype = e.get("type")
        if etype not in KEEP_EDGE_TYPES or etype not in REANCHOR_SOURCE_EDGES:
            continue
        src, tgt = e.get("source"), e.get("target")
        if src is None or tgt is None:
            continue
        if src in keep_ids:
            continue
        order = e.get("order")
        if order is None:
            order = _order_from_ancestors(src, parent, stmt_order)
        src = _reanchor_behavior_source(src, parent, all_nodes, keep_ids)
        if src is None or src not in keep_ids or tgt not in keep_ids:
            continue
        if src == tgt:
            continue
        key = _edge_key(src, tgt, etype, e)
        if key in index:
            continue
        _append_edge(src, tgt, etype, e, order, count=int(e.get("count") or 1))

    # Ensure Method/Constructor --HAS_VARIABLE--> Variable via DECLARED_IN inverse.
    for e in all_edges:
        if e.get("type") != "DECLARED_IN":
            continue
        var_id, owner_id = e.get("source"), e.get("target")
        if var_id not in keep_ids or owner_id not in keep_ids:
            continue
        if all_nodes.get(var_id, {}).get("type") != "Variable":
            continue
        if all_nodes.get(owner_id, {}).get("type") not in BEHAVIOR_OWNERS:
            continue
        key = (owner_id, var_id, "HAS_VARIABLE")
        if key in index:
            continue
        edge = {"source": owner_id, "target": var_id, "type": "HAS_VARIABLE", "count": 1}
        index[key] = edge
        edges.append(edge)

    return {
        "project": project_graph.get("project"),
        "labels": project_graph.get("labels"),
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "kind": "model_graph",
            "kept_node_types": sorted(KEEP_NODE_TYPES),
            "kept_edge_types": sorted(KEEP_EDGE_TYPES),
        },
    }


def _build_parent_index(edges: list[dict[str, Any]]) -> dict[str, str]:
    parent: dict[str, str] = {}
    for e in edges:
        if e.get("type") not in PARENT_EDGE_TYPES:
            continue
        if e["type"] == "DECLARED_IN":
            parent[e["source"]] = e["target"]
        elif e["type"] == "NEXT_STATEMENT":
            continue
        else:
            parent[e["target"]] = e["source"]
    return parent


def _order_from_ancestors(
    node_id: str,
    parent: dict[str, str],
    stmt_order: dict[str, Any],
) -> int | None:
    cur: str | None = node_id
    seen: set[str] = set()
    while cur and cur not in seen:
        if cur in stmt_order:
            return stmt_order[cur]
        seen.add(cur)
        cur = parent.get(cur)
    return None


def _reanchor_behavior_source(
    node_id: str,
    parent: dict[str, str],
    nodes: dict[str, dict[str, Any]],
    keep_ids: set[str],
) -> str | None:
    cur: str | None = node_id
    seen: set[str] = set()
    fallback: str | None = None
    while cur and cur not in seen:
        seen.add(cur)
        n = nodes.get(cur)
        if n and cur in keep_ids:
            if n.get("type") in BEHAVIOR_OWNERS:
                return cur
            if fallback is None:
                fallback = cur
        cur = parent.get(cur)
    return fallback
