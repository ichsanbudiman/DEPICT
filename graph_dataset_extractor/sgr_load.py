from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from constants import (
    COLLECTION_SIMPLE,
    FIELD_SGR,
    METHOD_SGR,
    PARAM_SGR,
    TYPE_SGR,
)


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


@dataclass
class SgrNode:
    id: str
    type: str
    name: str = ""
    qualified_name: str = ""
    external: bool = False
    is_collection: bool = False
    modifiers: Dict[str, bool] = field(default_factory=dict)
    return_type: str = ""
    datatype: str = ""
    generic: bool = False
    file: str = ""
    source: str = ""
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    signature: str = ""
    package: str = ""
    supertypes: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)


@dataclass
class SgrEdge:
    source: str
    target: str
    type: str
    via: Optional[str] = None
    via_kind: Optional[str] = None
    count: int = 1
    provenance: Optional[str] = None


@dataclass
class ProjectGraph:
    project_name: str
    nodes: Dict[str, SgrNode]
    edges: List[SgrEdge]
    out: Dict[str, List[SgrEdge]] = field(default_factory=lambda: defaultdict(list))
    inn: Dict[str, List[SgrEdge]] = field(default_factory=lambda: defaultdict(list))
    owner: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    has_type: Dict[str, str] = field(default_factory=dict)
    field_writers: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    field_delegators: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    methods_by_name: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    types_by_simple: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    types_by_qn: Dict[str, str] = field(default_factory=dict)

    def index(self) -> None:
        self.out = defaultdict(list)
        self.inn = defaultdict(list)
        self.owner = {}
        self.params = defaultdict(list)
        self.has_type = {}
        self.field_writers = defaultdict(list)
        self.field_delegators = defaultdict(list)
        self.methods_by_name = defaultdict(list)
        self.types_by_simple = defaultdict(list)
        self.types_by_qn = {}
        for nid, n in self.nodes.items():
            if n.type in METHOD_SGR and not n.external:
                self.methods_by_name[n.name.lower()].append(nid)
            elif n.type in TYPE_SGR and not n.external:
                if n.name:
                    self.types_by_simple[n.name].append(nid)
                if n.qualified_name:
                    self.types_by_qn[n.qualified_name] = nid
        for e in self.edges:
            self.out[e.source].append(e)
            self.inn[e.target].append(e)
            if e.type in {"HAS_METHOD", "HAS_CONSTRUCTOR", "HAS_FIELD"}:
                self.owner[e.target] = e.source
            elif e.type == "HAS_PARAMETER":
                self.params[e.source].append(e.target)
            elif e.type == "HAS_TYPE":
                self.has_type[e.source] = e.target
            elif e.type == "WRITES_FIELD":
                self.field_writers[e.target].append(e.source)
            elif e.type == "DELEGATES_VIA" and e.via_kind == "field" and e.via:
                self.field_delegators[e.via].append(e.source)

    def node(self, nid: str) -> Optional[SgrNode]:
        return self.nodes.get(nid)

    def is_type(self, nid: str) -> bool:
        n = self.nodes.get(nid)
        return bool(n and n.type in TYPE_SGR and not n.external)

    def is_method(self, nid: str) -> bool:
        n = self.nodes.get(nid)
        return bool(n and n.type in METHOD_SGR)

    def is_field(self, nid: str) -> bool:
        n = self.nodes.get(nid)
        return bool(n and n.type in FIELD_SGR)

    def is_param(self, nid: str) -> bool:
        n = self.nodes.get(nid)
        return bool(n and n.type == PARAM_SGR)

    def is_external_method(self, nid: str) -> bool:
        n = self.nodes.get(nid)
        if not n:
            return nid.startswith("EM:")
        return n.type == "Method" and (n.external or nid.startswith("EM:"))

    def members_of(self, type_id: str) -> List[str]:
        out = []
        for e in self.out.get(type_id, ()):
            if e.type in {"HAS_METHOD", "HAS_CONSTRUCTOR", "HAS_FIELD"}:
                out.append(e.target)
        return out

    def methods_of(self, type_id: str) -> List[str]:
        return [m for m in self.members_of(type_id) if self.is_method(m)]

    def fields_of(self, type_id: str) -> List[str]:
        return [m for m in self.members_of(type_id) if self.is_field(m)]

    def owner_type(self, member_id: str) -> Optional[str]:
        return self.owner.get(member_id)

    def simple_name(self, nid: str) -> str:
        n = self.nodes.get(nid)
        if n and n.name:
            return n.name
        if ":" in nid:
            tail = nid.split(":", 1)[1]
            return tail.rsplit(".", 1)[-1]
        return nid

    def is_jdk_collection_type(self, nid: str) -> bool:
        n = self.nodes.get(nid)
        if not n:
            return False
        if n.type not in {"ExternalType", "Class", "Interface"}:
            return False
        name = (n.name or self.simple_name(nid)).lower()
        return name in COLLECTION_SIMPLE

    def is_collection_field(self, field_id: str) -> bool:
        n = self.nodes.get(field_id)
        if n and n.is_collection:
            return True
        tid = self.has_type.get(field_id)
        return bool(tid and self.is_jdk_collection_type(tid))

    def methods_named(self, name: str) -> Iterable[str]:
        return list(self.methods_by_name.get(name.lower(), ()))


def load_project_yaml(path: Path) -> ProjectGraph:
    nodes: Dict[str, SgrNode] = {}
    edges: List[SgrEdge] = []
    project_name = ""
    cur: Dict[str, str] = {}

    def flush() -> None:
        if not cur:
            return
        if "id" in cur:
            nid = cur["id"]
            mods = {}
            for k, v in cur.items():
                if k.startswith("mod."):
                    mods[k[4:]] = v in {"true", "True", "yes"}
            nodes[nid] = SgrNode(
                id=nid,
                type=cur.get("type", ""),
                name=cur.get("name", ""),
                qualified_name=cur.get("qualified_name", ""),
                external=cur.get("external", "") in {"true", "True", "yes"},
                is_collection=cur.get("is_collection", "") in {"true", "True", "yes"},
                modifiers=mods,
                return_type=cur.get("return_type", ""),
                datatype=cur.get("datatype", ""),
                generic=cur.get("generic", "") in {"true", "True", "yes"},
            )
        elif "source" in cur and "target" in cur:
            edges.append(
                SgrEdge(
                    source=cur["source"],
                    target=cur["target"],
                    type=cur.get("type", ""),
                    via=cur.get("via") or None,
                    via_kind=cur.get("via_kind") or None,
                )
            )

    with path.open(errors="replace") as f:
        for line in f:
            if line.startswith("  name:") and not cur and not project_name:
                # project.name under project: block — only before nodes
                pass
            if line.startswith("project:"):
                continue
            if line.startswith("  name:") and "id" not in cur and "source" not in cur:
                if not project_name:
                    project_name = _unquote(line.split(":", 1)[1])
                continue
            if line.startswith("nodes:") or line.startswith("edges:"):
                flush()
                cur = {}
                continue
            if line.startswith("- id:"):
                flush()
                cur = {"id": _unquote(line.split(":", 1)[1])}
                continue
            if line.startswith("- source:"):
                flush()
                cur = {"source": _unquote(line.split(":", 1)[1])}
                continue
            if not cur:
                continue
            raw = line.rstrip("\n")
            if raw.startswith("    ") and ":" in raw and cur.get("_mods"):
                key, _, val = raw.strip().partition(":")
                cur[f"mod.{key}"] = _unquote(val)
                continue
            if raw.startswith("  ") and not raw.startswith("    "):
                cur.pop("_mods", None)
            if raw.startswith("  ") and ":" in raw:
                key, _, val = raw.strip().partition(":")
                key, val = key.strip(), _unquote(val)
                if key == "modifiers":
                    cur["_mods"] = "1"
                    continue
                if key == "generic_parameters":
                    cur["generic"] = "false" if val in {"", "[]"} else "true"
                    continue
                if key in {
                    "type",
                    "name",
                    "qualified_name",
                    "target",
                    "via",
                    "via_kind",
                    "external",
                    "is_collection",
                    "return_type",
                    "datatype",
                }:
                    cur[key] = val
        flush()

    g = ProjectGraph(project_name=project_name, nodes=nodes, edges=edges)
    g.index()
    return g


def _as_bool(value: object) -> bool:
    return value in {True, "true", "True", "yes"}


def _node_from_yaml(raw: dict) -> SgrNode:
    mods = raw.get("modifiers") or {}
    if not isinstance(mods, dict):
        mods = {}
    supers = raw.get("supertypes") or []
    anns = raw.get("annotations") or []
    return SgrNode(
        id=str(raw.get("id") or ""),
        type=str(raw.get("type") or ""),
        name=str(raw.get("name") or ""),
        qualified_name=str(raw.get("qualified_name") or ""),
        external=_as_bool(raw.get("external")),
        is_collection=_as_bool(raw.get("is_collection")),
        modifiers={str(k): _as_bool(v) for k, v in mods.items()},
        return_type=str(raw.get("return_type") or ""),
        datatype=str(raw.get("datatype") or ""),
        generic=bool(raw.get("generic_parameters")),
        file=str(raw.get("file") or ""),
        source=raw.get("source") or "",
        line_start=raw.get("line_start"),
        line_end=raw.get("line_end"),
        signature=str(raw.get("signature") or ""),
        package=str(raw.get("package") or ""),
        supertypes=[str(s) for s in supers] if isinstance(supers, list) else [],
        annotations=[str(a) for a in anns] if isinstance(anns, list) else [],
    )


def _edge_from_yaml(raw: dict) -> SgrEdge:
    return SgrEdge(
        source=str(raw.get("source") or ""),
        target=str(raw.get("target") or ""),
        type=str(raw.get("type") or ""),
        via=raw.get("via") or None,
        via_kind=raw.get("via_kind") or None,
        count=int(raw.get("count") or 1),
        provenance=raw.get("provenance") or None,
    )


def load_project_model(path: Path) -> ProjectGraph:
    """Load sgr_project.yaml with member source, signature, and edge facts."""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    project = data.get("project") or {}
    name = project.get("name") if isinstance(project, dict) else ""
    nodes = {
        n.id: n
        for raw in (data.get("nodes") or [])
        if isinstance(raw, dict) and raw.get("id")
        for n in (_node_from_yaml(raw),)
    }
    edges = [
        _edge_from_yaml(raw)
        for raw in (data.get("edges") or [])
        if isinstance(raw, dict) and raw.get("source") and raw.get("target")
    ]
    g = ProjectGraph(project_name=str(name or ""), nodes=nodes, edges=edges)
    g.index()
    return g


def load_metadata_document(path: Path) -> dict:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def load_metadata_types(path: Path) -> Dict[str, str]:
    """rel file path → type id from metadata.yaml symbols.classes."""
    by_file: Dict[str, str] = {}
    section = None
    cur: Dict[str, str] = {}

    def flush() -> None:
        if section == "classes" and cur.get("file") and cur.get("id"):
            by_file[cur["file"]] = cur["id"]

    with path.open(errors="replace") as f:
        for line in f:
            if line.startswith("  classes:"):
                flush()
                section = "classes"
                cur = {}
                continue
            if line.startswith("  methods:") or line.startswith("dpd:"):
                flush()
                section = None
                cur = {}
                continue
            if section != "classes":
                continue
            if line.startswith("    ") and not line.startswith("      ") and line.strip().endswith(":"):
                flush()
                cur = {}
                continue
            if cur is not None and line.startswith("      file:"):
                cur["file"] = _unquote(line.split(":", 1)[1])
            elif cur is not None and line.startswith("      id:"):
                cur["id"] = _unquote(line.split(":", 1)[1])
        flush()
    return by_file
