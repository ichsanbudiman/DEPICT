"""
v1 is the original collaborating-member walk. v2 is the paper's
collaboration neighbourhood. Neither version reads the pattern label.
See NEIGHBOURHOOD.md.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, Iterable, List, Optional, Set, Tuple

from constants import IGNORE_SGR, KIND_FIELD, KIND_METHOD, KIND_TYPE, MEMBER_MEMBER, SGR_TO_V5
from sgr_load import ProjectGraph

KeepSets = Tuple[Set[str], Set[str], Set[str]]  # types, methods, fields

NEIGHBOURHOOD_V1 = "v1"
NEIGHBOURHOOD_V2 = "v2"
NEIGHBOURHOODS = (NEIGHBOURHOOD_V1, NEIGHBOURHOOD_V2)


def parse_neighbourhood(value: str) -> str:
    key = (value or "").strip().lower()
    if key not in NEIGHBOURHOODS:
        names = ", ".join(NEIGHBOURHOODS)
        raise ValueError(f"neighbourhood must be one of {names}, got {value!r}")
    return key


def is_test_or_sample(g: ProjectGraph, type_id: str) -> bool:
    n = g.node(type_id)
    qn = (n.qualified_name if n else "") or type_id.split(":", 1)[-1]
    name = (n.name if n else qn.rsplit(".", 1)[-1])
    pkg = qn.rsplit(".", 1)[0].lower() if "." in qn else ""
    pkg_parts = set(pkg.replace("/", ".").split("."))
    if pkg_parts & {"test", "tests", "samples", "sample"}:
        return True
    if name.endswith("Tests"):
        return True
    if name.endswith("Test") and name not in {"Test"}:
        return True
    return False


def _project_type(g: ProjectGraph, nid: Optional[str]) -> Optional[str]:
    if nid and g.is_type(nid):
        return nid
    return None

_LIBRARY_SIMPLE = frozenset({
    "String", "StringBuffer", "StringBuilder", "Object", "Arrays", "System", "Math",
    "Integer", "Long", "Boolean", "Double", "Float", "Byte", "Short", "Character",
    "Class", "Thread", "Throwable", "Exception", "RuntimeException", "Error",
    "StringTokenizer", "Vector", "Hashtable", "HashMap", "TreeMap", "TreeSet",
    "ArrayList", "LinkedList", "HashSet", "Collections", "List", "Set", "Map",
    "Collection", "Iterator", "Enumeration", "Date", "DataInput", "DataOutput",
    "InputStream", "OutputStream", "Reader", "Writer", "Logger", "Level", "URL",
    "URI", "File", "Locale", "Pattern", "Matcher", "Comparator", "MessageDigest",
    "BigInteger", "Properties", "Calendar", "Random", "PrintStream", "PrintWriter",
    "IOException", "Runtime", "Process", "StringWriter", "StringReader",
    "CharSequence", "Number", "Enum", "Objects", "Optional", "Comparable",
})


def _package_root(qualified: str) -> str:
    parts = qualified.split(".")
    if len(parts) < 2:
        return ""
    package = parts[:-1]
    if len(package) >= 2:
        return ".".join(package[:2])
    return package[0]


def _owner_and_name(qualified: str) -> Optional[Tuple[str, str]]:
    if not qualified or "." not in qualified:
        return None
    if any(ch in qualified for ch in "[]() \t\"'"):
        return None
    owner, name = qualified.rsplit(".", 1)
    if not owner or not name:
        return None
    if not (name[0].isalpha() or name[0] == "_"):
        return None
    if not all(ch.isalnum() or ch == "_" for ch in name):
        return None
    if not all(ch.isalnum() or ch in "._" for ch in owner):
        return None
    return owner, name


def _type_for_receiver(g: ProjectGraph, owner_text: str) -> Optional[str]:
    exact = g.types_by_qn.get(owner_text)
    if exact and g.is_type(exact):
        return exact
    simple = owner_text.split(".")[-1]
    if not simple or not simple[0].isupper() or simple in _LIBRARY_SIMPLE:
        return None
    hits = [tid for tid in g.types_by_simple.get(simple, ()) if g.is_type(tid)]
    if len(hits) != 1:
        return None
    if "." not in owner_text:
        return hits[0]
    type_qn = (g.node(hits[0]).qualified_name if g.node(hits[0]) else "") or ""
    root = _package_root(owner_text)
    if root and root == _package_root(type_qn):
        return hits[0]
    return None


def _method_on_type(g: ProjectGraph, type_id: str, name: str) -> Optional[str]:
    hits = [mid for mid in g.methods_of(type_id) if g.simple_name(mid) == name]
    if len(hits) == 1:
        return hits[0]
    return None


def resolve_callee(
    g: ProjectGraph, target: str, candidate_types: Optional[Set[str]] = None
) -> Optional[str]:
    if g.is_method(target) and not g.is_external_method(target):
        return target
    if not g.is_external_method(target) and not target.startswith("EM:"):
        return None
    node = g.node(target)
    qualified = (node.qualified_name if node else "") or ""
    if not qualified and target.startswith("EM:"):
        qualified = target[3:]
    parsed = _owner_and_name(qualified)
    if not parsed:
        return None
    owner_text, name = parsed
    type_id = _type_for_receiver(g, owner_text)
    if not type_id:
        return None
    if candidate_types is not None and type_id not in candidate_types:
        return None
    return _method_on_type(g, type_id, name)


def project_call_targets(
    g: ProjectGraph, methods: Iterable[str]
) -> Tuple[Set[str], Set[str]]:
    found_methods: Set[str] = set()
    found_types: Set[str] = set()
    for mid in methods:
        for edge in g.out.get(mid, ()):
            if edge.type not in {"CALLS", "DELEGATES_VIA"}:
                continue
            dst = resolve_callee(g, edge.target, None)
            if not dst or dst == mid:
                continue
            owner = g.owner_type(dst)
            if not owner or not g.is_type(owner) or is_test_or_sample(g, owner):
                continue
            found_methods.add(dst)
            found_types.add(owner)
    return found_methods, found_types


def assert_type_hops(feats: Dict[str, dict], types: Iterable[str], max_hops: int) -> None:
    bad = []
    for tid in types:
        hop = (feats.get(tid) or {}).get("hop_distance", -1)
        if not isinstance(hop, (int, float)) or hop < 0 or hop > max_hops:
            bad.append(f"{tid} hop={hop}")
    if bad:
        shown = ", ".join(bad[:8])
        raise AssertionError(f"type hop exceeds k={max_hops}: {shown}")


def assert_no_call_self_loops(edges: List[dict]) -> None:
    bad = [
        edge["src_id"]
        for edge in edges
        if edge.get("src_id") == edge.get("dst_id")
        and {"calls", "delegates"} & set(edge.get("relation_type") or ())
    ]
    if bad:
        shown = ", ".join(bad[:5])
        raise AssertionError(f"calls or delegates edge is a self-loop: {shown}")


def _project_type_named(g: ProjectGraph, text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    exact = g.types_by_qn.get(text)
    if exact and g.is_type(exact):
        return exact
    simple = text.split(".")[-1]
    if not simple or not simple[0].isupper() or simple in _LIBRARY_SIMPLE:
        return None
    hits = [tid for tid in g.types_by_simple.get(simple, ()) if g.is_type(tid)]
    if len(hits) == 1:
        return hits[0]
    return None


def infer_element_types(g: ProjectGraph, field_id: str) -> Set[str]:
    node = g.node(field_id)
    datatype = (node.datatype if node else "") or ""
    if "[]" in datatype:
        component = datatype.split("[]", 1)[0].strip()
        if "<" in component:
            component = component.split("<", 1)[0].strip()
        named = _project_type_named(g, component)
        return {named} if named else set()
    elems: Set[str] = set()
    for edge in g.out.get(field_id, ()):
        if edge.type != "HAS_ELEMENT_TYPE":
            continue
        project = _project_type(g, edge.target)
        if project:
            elems.add(project)
    return elems


def ancestor_types(g: ProjectGraph, type_id: str) -> Set[str]:
    seen: Set[str] = set()
    stack = [type_id]
    while stack:
        u = stack.pop()
        for e in g.out.get(u, ()):
            if e.type not in {"EXTENDS", "IMPLEMENTS"}:
                continue
            t = _project_type(g, e.target)
            if t and t not in seen:
                seen.add(t)
                stack.append(t)
    return seen


def adjacent_types(g: ProjectGraph, type_id: str, incoming: bool) -> Set[str]:
    found: Set[str] = set()
    bucket = g.inn if incoming else g.out
    for edge in bucket.get(type_id, ()):
        if edge.type not in {"EXTENDS", "IMPLEMENTS"}:
            continue
        other = edge.source if incoming else edge.target
        project = _project_type(g, other)
        if project:
            found.add(project)
    return found


def descendant_types(g: ProjectGraph, type_id: str) -> Set[str]:
    seen: Set[str] = set()
    stack = [type_id]
    while stack:
        u = stack.pop()
        for e in g.inn.get(u, ()):
            if e.type not in {"EXTENDS", "IMPLEMENTS"}:
                continue
            t = _project_type(g, e.source)
            if t and t not in seen:
                seen.add(t)
                stack.append(t)
    return seen


def method_ref_types(g: ProjectGraph, mid: str) -> Set[str]:
    types: Set[str] = set()
    for pr in g.params.get(mid, ()):
        t = _project_type(g, g.has_type.get(pr))
        if t:
            types.add(t)
    return types


def method_use_types(g: ProjectGraph, mid: str) -> Set[str]:
    types: Set[str] = set()
    for e in g.out.get(mid, ()):
        if e.type in {"RETURNS", "RETURNS_ELEMENT"}:
            t = _project_type(g, e.target)
            if t:
                types.add(t)
    return types


def field_collab_types(g: ProjectGraph, fid: str) -> Set[str]:
    types: Set[str] = set()
    ht = g.has_type.get(fid)
    if ht and not g.is_jdk_collection_type(ht):
        t = _project_type(g, ht)
        if t:
            types.add(t)
    types |= infer_element_types(g, fid)
    return types


def owner_holds_gold(g: ProjectGraph, owner: Optional[str], gold: str) -> bool:
    if not owner:
        return False
    for fid in g.fields_of(owner):
        if gold in field_collab_types(g, fid):
            return True
    return False


def method_creates_type(g: ProjectGraph, mid: str, tid: str) -> bool:
    for e in g.out.get(mid, ()):
        if e.type == "CREATES" and _project_type(g, e.target) == tid:
            return True
    return False


def method_is_factory_like(g: ProjectGraph, mid: str) -> bool:
    if method_use_types(g, mid):
        return True
    for e in g.out.get(mid, ()):
        if e.type != "CREATES":
            continue
        t = _project_type(g, e.target)
        if t and created_pairs_with_uses(g, mid, t):
            return True
    return False


def created_pairs_with_uses(g: ProjectGraph, mid: str, created: str) -> bool:
    used = method_use_types(g, mid)
    if created in used:
        return True
    created_anc = ancestor_types(g, created) | {created}
    return bool(used & created_anc)


def _resolve_project_method(
    g: ProjectGraph, target: str, seed_types: Set[str]
) -> Optional[str]:
    if g.is_method(target) and not g.is_external_method(target):
        return target
    if g.is_external_method(target) or target.startswith("EM:"):
        return resolve_callee(g, target, seed_types)
    return None


def _method_signals(
    g: ProjectGraph, mid: str, gold: str, seed_types: Set[str]
) -> Tuple[bool, bool]:
    other_types = {t for t in (method_ref_types(g, mid) | method_use_types(g, mid)) if t != gold}
    field_other = False
    cross_call = False
    paired_create = False
    for e in g.out.get(mid, ()):
        if e.type in {"READS_FIELD", "WRITES_FIELD"}:
            if any(t != gold for t in field_collab_types(g, e.target)):
                field_other = True
        elif e.type == "DELEGATES_VIA":
            resolved = _resolve_project_method(g, e.target, seed_types)
            if resolved:
                owner = g.owner_type(resolved)
                if owner and owner != gold and g.is_type(owner):
                    cross_call = True
            if e.via_kind == "field" and e.via:
                if any(t != gold for t in field_collab_types(g, e.via)):
                    field_other = True
        elif e.type == "CALLS":
            resolved = _resolve_project_method(g, e.target, seed_types)
            if resolved:
                owner = g.owner_type(resolved)
                if owner and owner != gold and g.is_type(owner):
                    cross_call = True
        elif e.type in {"OVERRIDES", "IMPLEMENTS_METHOD"}:
            # Alone this does not collaborate (God-class interface dump).
            # Adapter/Visitor still qualify via delegates / references.
            pass
        elif e.type == "CREATES":
            t = _project_type(g, e.target)
            if t and t != gold and created_pairs_with_uses(g, mid, t):
                paired_create = True
    collaborates = bool(other_types or field_other or cross_call or paired_create)
    wants_incoming = bool(
        {t for t in method_ref_types(g, mid) if t != gold}
        or {t for t in method_use_types(g, mid) if t != gold}
        or field_other
        or cross_call
        or paired_create
    )
    return collaborates, wants_incoming


def _keep_once(g: ProjectGraph, gold: str, drop_tests: bool) -> KeepSets:
    if not g.is_type(gold):
        raise ValueError(f"gold root is not a project type: {gold}")

    keep_types: Set[str] = {gold}
    keep_methods: Set[str] = set()
    keep_fields: Set[str] = set()

    def maybe_type(tid: Optional[str]) -> None:
        t = _project_type(g, tid)
        if not t:
            return
        if drop_tests and t != gold and is_test_or_sample(g, t):
            return
        keep_types.add(t)

    def touch_method(mid: Optional[str], seed: Set[str]) -> Optional[str]:
        if not mid:
            return None
        if g.is_external_method(mid) or mid.startswith("EM:"):
            mid = resolve_callee(g, mid, seed | keep_types)
            if not mid:
                return None
        if not g.is_method(mid) or g.is_external_method(mid):
            return None
        owner = g.owner_type(mid)
        if drop_tests and owner and owner != gold and is_test_or_sample(g, owner):
            return None
        keep_methods.add(mid)
        maybe_type(owner)
        return mid

    def touch_field(fid: str) -> None:
        if not g.is_field(fid):
            return
        owner = g.owner_type(fid)
        if drop_tests and owner and owner != gold and is_test_or_sample(g, owner):
            return
        keep_fields.add(fid)
        maybe_type(owner)

    # Gold hierarchy, hop ≤ 1 (direct super / incoming subtype only)
    for e in g.out.get(gold, ()):
        if e.type in {"EXTENDS", "IMPLEMENTS"}:
            maybe_type(e.target)
    for e in g.inn.get(gold, ()):
        if e.type in {"EXTENDS", "IMPLEMENTS"}:
            maybe_type(e.source)

    collab_fields = [fid for fid in g.fields_of(gold) if any(t != gold for t in field_collab_types(g, fid))]
    seed_types = set(keep_types)
    for fid in collab_fields:
        seed_types |= field_collab_types(g, fid)

    collab_methods: List[str] = []
    incoming_methods: List[str] = []
    for mid in g.methods_of(gold):
        collab, incoming = _method_signals(g, mid, gold, seed_types)
        if collab:
            collab_methods.append(mid)
        if incoming:
            incoming_methods.append(mid)

    def expand_method(mid: str, seed: Set[str], gated_creates: bool) -> None:
        for t in method_ref_types(g, mid) | method_use_types(g, mid):
            maybe_type(t)
        for e in g.out.get(mid, ()):
            if e.type in MEMBER_MEMBER:
                touch_method(e.target, seed)
            elif e.type == "CREATES" and gated_creates:
                t = _project_type(g, e.target)
                if t and t != gold and created_pairs_with_uses(g, mid, t):
                    maybe_type(t)
            elif e.type in {"READS_FIELD", "WRITES_FIELD"}:
                if e.target in keep_fields or any(t != gold for t in field_collab_types(g, e.target)):
                    touch_field(e.target)
            if e.type == "DELEGATES_VIA" and e.via_kind == "field" and e.via:
                if e.via in keep_fields or any(t != gold for t in field_collab_types(g, e.via)):
                    touch_field(e.via)

    for fid in collab_fields:
        touch_field(fid)
        for t in field_collab_types(g, fid):
            maybe_type(t)

    seed = set(keep_types) | seed_types
    for mid in collab_methods:
        touch_method(mid, seed)
        expand_method(mid, seed, gated_creates=True)

    seed = set(keep_types)
    # Retry EM resolve now that hop-1 types are known
    for mid in collab_methods:
        expand_method(mid, seed, gated_creates=True)

    # Subtype overrides of factory-like gold methods only (uses a product).
    # Do not walk every implements_method on AbstractFigure subclasses.
    subtype_targets = {mid for mid in collab_methods if method_is_factory_like(g, mid)}
    subtype_names = {g.simple_name(mid) for mid in subtype_targets}
    incoming_subtypes = {
        e.source
        for e in g.inn.get(gold, ())
        if e.type in {"EXTENDS", "IMPLEMENTS"} and _project_type(g, e.source)
    }
    for tid in list(keep_types):
        if tid == gold:
            continue
        for mid in g.methods_of(tid):
            for e in g.out.get(mid, ()):
                if e.type not in {"OVERRIDES", "IMPLEMENTS_METHOD"}:
                    continue
                if not (
                    e.target in subtype_targets
                    or (tid in incoming_subtypes and g.simple_name(mid) in subtype_names)
                ):
                    continue
                if not method_is_factory_like(g, mid):
                    continue
                touch_method(mid, seed)
                expand_method(mid, seed, gated_creates=True)

    # Typed close before incoming so accept owners are already hop-1.
    # visit references T (often ASTNode); accept(Visitor) lives on subtypes of T.
    # Keep only those methods and their owners — not every member of the subtype.
    family = {gold} | ancestor_types(g, gold)
    desc_cache: Dict[str, Set[str]] = {}
    for mid in collab_methods:
        for t in method_ref_types(g, mid):
            maybe_type(t)
            if t not in desc_cache:
                desc_cache[t] = descendant_types(g, t)
            for t2 in {t} | desc_cache[t]:
                if drop_tests and t2 != gold and is_test_or_sample(g, t2):
                    continue
                for m2 in g.methods_of(t2):
                    hit = method_ref_types(g, m2) & family
                    if hit:
                        touch_method(m2, seed)
                        for vis in hit:
                            maybe_type(vis)

    hop1 = set(keep_types)

    def incoming_caller_ok(src: str) -> bool:
        if g.is_external_method(src) or src.startswith("EM:"):
            src2 = resolve_callee(g, src, seed | keep_types)
            if not src2:
                return False
            src = src2
        if not g.is_method(src) or g.is_external_method(src):
            return False
        owner = g.owner_type(src)
        if owner and owner in hop1:
            return True
        if method_creates_type(g, src, gold):
            return True
        if owner_holds_gold(g, owner, gold):
            return True
        return False

    incoming_targets = set(incoming_methods)
    for mid in incoming_methods:
        for e in g.out.get(mid, ()):
            if e.type in {"IMPLEMENTS_METHOD", "OVERRIDES"}:
                resolved = touch_method(e.target, seed)
                if resolved:
                    incoming_targets.add(resolved)
    for tgt in incoming_targets:
        for e in g.inn.get(tgt, ()):
            if e.type in {"CALLS", "DELEGATES_VIA"} and incoming_caller_ok(e.source):
                touch_method(e.source, seed)

    for mid in list(keep_methods):
        for e in g.out.get(mid, ()):
            if e.type in {"READS_FIELD", "WRITES_FIELD"}:
                if e.target in keep_fields or g.owner_type(e.target) in keep_types:
                    if e.target in keep_fields or any(
                        x != gold for x in field_collab_types(g, e.target)
                    ):
                        touch_field(e.target)
            elif e.type == "DELEGATES_VIA" and e.via_kind == "field" and e.via:
                if g.owner_type(e.via) in keep_types or e.via in keep_fields:
                    touch_field(e.via)

    return keep_types, keep_methods, keep_fields


def _reached_types(g: ProjectGraph, mid: str, cache: Dict[str, Set[str]]) -> Set[str]:
    """Types a member reaches by the paper's rules (1)--(3).

    ``creates`` is not included. ``overrides`` and ``implements_method`` are
    not included. The member's own declaring type is not included.
    """
    hit = cache.get(mid)
    if hit is not None:
        return hit
    owner = g.owner_type(mid)
    found: Set[str] = set()

    def add(tid: Optional[str]) -> None:
        project = _project_type(g, tid)
        if project and project != owner:
            found.add(project)

    if g.is_field(mid):
        for tid in field_collab_types(g, mid):
            add(tid)
        cache[mid] = found
        return found
    if not g.is_method(mid):
        cache[mid] = found
        return found

    for tid in method_ref_types(g, mid) | method_use_types(g, mid):
        add(tid)
    for edge in g.out.get(mid, ()):
        if edge.type in {"READS_FIELD", "WRITES_FIELD"}:
            for tid in field_collab_types(g, edge.target):
                add(tid)
        elif edge.type == "DELEGATES_VIA" and edge.via_kind == "field" and edge.via:
            for tid in field_collab_types(g, edge.via):
                add(tid)

    # Bind an unresolved call only onto a type this member already reaches.
    # The root's supertypes are not candidates: a call named write must not
    # land on Writable.write just because the root implements Writable.
    for edge in g.out.get(mid, ()):
        if edge.type not in {"CALLS", "DELEGATES_VIA"}:
            continue
        resolved = _resolve_project_method(g, edge.target, set(found))
        if resolved:
            add(g.owner_type(resolved))
    cache[mid] = found
    return found


def _creates_allowed(
    g: ProjectGraph, mid: str, created: str, cache: Dict[str, Set[str]]
) -> bool:
    """True when rules (1)--(3) already reach ``created`` or a supertype of it."""
    family = ancestor_types(g, created) | {created}
    return bool(_reached_types(g, mid, cache) & family)


def _keep_once_v2(g: ProjectGraph, gold: str, drop_tests: bool) -> KeepSets:
    if not g.is_type(gold):
        raise ValueError(f"gold root is not a project type: {gold}")

    keep_types: Set[str] = set()
    keep_methods: Set[str] = set()
    keep_fields: Set[str] = set()
    cache: Dict[str, Set[str]] = {}

    def add_type(tid: Optional[str]) -> bool:
        project = _project_type(g, tid)
        if not project:
            return False
        if drop_tests and project != gold and is_test_or_sample(g, project):
            return False
        keep_types.add(project)
        return True

    def attach_method(target: Optional[str], seed: Set[str]) -> Optional[str]:
        if not target:
            return None
        resolved = _resolve_project_method(g, target, seed)
        if not resolved or not g.is_method(resolved) or g.is_external_method(resolved):
            return None
        owner = g.owner_type(resolved)
        if not owner or owner == gold or not add_type(owner):
            return None
        keep_methods.add(resolved)
        return resolved

    def attach_field(fid: Optional[str]) -> None:
        if not fid or not g.is_field(fid) or not field_collab_types(g, fid):
            return
        owner = g.owner_type(fid)
        if not owner or owner == gold or not add_type(owner):
            return
        keep_fields.add(fid)

    add_type(gold)
    for tid in adjacent_types(g, gold, incoming=False):
        add_type(tid)
    for tid in adjacent_types(g, gold, incoming=True):
        add_type(tid)

    collab_fields = [fid for fid in g.fields_of(gold) if _reached_types(g, fid, cache)]
    collab_methods = [mid for mid in g.methods_of(gold) if _reached_types(g, mid, cache)]
    keep_fields.update(collab_fields)
    keep_methods.update(collab_methods)

    for mid in collab_fields + collab_methods:
        for tid in _reached_types(g, mid, cache):
            add_type(tid)
        for edge in g.out.get(mid, ()):
            if edge.type != "CREATES":
                continue
            created = _project_type(g, edge.target)
            if created and _creates_allowed(g, mid, created, cache):
                add_type(created)

    call_targets = set(collab_methods)
    for mid in collab_methods:
        reached = _reached_types(g, mid, cache)
        for edge in g.out.get(mid, ()):
            if edge.type in {"CALLS", "DELEGATES_VIA"}:
                attach_method(edge.target, reached)
                if edge.type == "DELEGATES_VIA" and edge.via_kind == "field" and edge.via:
                    attach_field(edge.via)
            elif edge.type in {"READS_FIELD", "WRITES_FIELD"}:
                attach_field(edge.target)
            elif edge.type in {"OVERRIDES", "IMPLEMENTS_METHOD"}:
                fulfilled = attach_method(edge.target, reached)
                if fulfilled:
                    call_targets.add(fulfilled)

    returning = {mid for mid in collab_methods if method_use_types(g, mid)}
    for subtype in adjacent_types(g, gold, incoming=True):
        if subtype not in keep_types:
            continue
        for mid in g.methods_of(subtype):
            overrides_returning = False
            for edge in g.out.get(mid, ()):
                if edge.type in {"OVERRIDES", "IMPLEMENTS_METHOD"} and edge.target in returning:
                    overrides_returning = True
                    break
            if not overrides_returning:
                continue
            attach_method(mid, keep_types)
            for edge in g.out.get(mid, ()):
                if edge.type != "CREATES":
                    continue
                created = _project_type(g, edge.target)
                if created and _creates_allowed(g, mid, created, cache):
                    add_type(created)

    ancestors = {gold} | ancestor_types(g, gold)
    touched: Set[str] = set()
    for mid in collab_fields + collab_methods:
        touched |= _reached_types(g, mid, cache)
    for tid in touched:
        for other in {tid} | descendant_types(g, tid):
            if drop_tests and other != gold and is_test_or_sample(g, other):
                continue
            for mid in g.methods_of(other):
                if g.owner_type(mid) == gold:
                    continue
                if _reached_types(g, mid, cache) & ancestors:
                    attach_method(mid, keep_types)

    visible = set(keep_types)
    for target in call_targets:
        for edge in g.inn.get(target, ()):
            if edge.type not in {"CALLS", "DELEGATES_VIA"}:
                continue
            caller = edge.source
            if g.is_external_method(caller) or caller.startswith("EM:"):
                caller = _resolve_project_method(g, caller, visible) or ""
            if not caller or not g.is_method(caller) or g.is_external_method(caller):
                continue
            owner = g.owner_type(caller)
            if not owner or owner == gold or owner not in visible:
                continue
            if drop_tests and is_test_or_sample(g, owner):
                continue
            keep_methods.add(caller)

    return keep_types, keep_methods, keep_fields


def keep_set(
    g: ProjectGraph,
    gold: str,
    drop_tests: bool,
    max_hops: int = 1,
    neighbourhood: str = NEIGHBOURHOOD_V1,
) -> KeepSets:
    # Collaborating-member keep-set for ``neighbourhood`` (``v1`` or ``v2``)
    version = parse_neighbourhood(neighbourhood)
    if max_hops < 1:
        raise ValueError(f"max_hops must be >= 1, got {max_hops}")
    step = _keep_once if version == NEIGHBOURHOOD_V1 else _keep_once_v2
    types, methods, fields = step(g, gold, drop_tests)
    if max_hops == 1:
        return types, methods, fields
    frontier = set(types)
    expanded = {gold}
    for _step in range(1, max_hops):
        nxt: Set[str] = set()
        for tid in frontier:
            if tid in expanded:
                continue
            expanded.add(tid)
            t2, m2, f2 = step(g, tid, drop_tests)
            for tid2 in t2:
                if tid2 not in types:
                    nxt.add(tid2)
            types |= t2
            methods |= m2
            fields |= f2
        frontier = nxt
        if not frontier:
            break
    return types, methods, fields


def kind_of(g: ProjectGraph, nid: str) -> Optional[str]:
    if g.is_type(nid):
        return KIND_TYPE
    if g.is_method(nid):
        return KIND_METHOD
    if g.is_field(nid):
        return KIND_FIELD
    return None


# Order used by depict_annotation_v1 edges.
REL_ORDER = (
    "has_field",
    "has_constructor",
    "has_method",
    "has",
    "has_element",
    "inherits",
    "implements",
    "reads_field",
    "writes_field",
    "delegates_via_field",
    "calls",
    "delegates",
    "overrides",
    "implements_method",
    "uses",
    "uses_element",
    "references",
    "creates",
)
_REL_RANK = {name: i for i, name in enumerate(REL_ORDER)}
_BEHAVIOR = frozenset({
    "calls",
    "delegates",
    "reads_field",
    "writes_field",
    "delegates_via_field",
})
_PROVENANCE_RANK = {"parameter": 3, "creation": 2, "other": 1}


def _relation_list(rels: Set[str]) -> List[str]:
    return sorted(rels, key=lambda name: (_REL_RANK.get(name, 100), name))


def _edge_facts(rels: Set[str], meta: dict) -> Optional[dict]:
    if not (rels & _BEHAVIOR):
        return None
    counts: Dict[str, int] = meta.get("counts") or {}
    facts: Dict[str, object] = {}
    if "reads_field" in rels and "writes_field" in rels:
        facts["count"] = counts.get("reads_field", 0) + counts.get("writes_field", 0)
        if meta.get("provenance"):
            facts["provenance"] = meta["provenance"]
    elif "calls" in rels or "delegates" in rels:
        facts["count"] = max(counts.get("calls", 0), counts.get("delegates", 0), 1)
        if meta.get("via"):
            facts["via"] = meta["via"]
            if meta.get("via_kind"):
                facts["via_kind"] = meta["via_kind"]
    else:
        present = [counts[r] for r in rels if r in counts]
        facts["count"] = max(present) if present else 1
        if "writes_field" in rels and meta.get("provenance"):
            facts["provenance"] = meta["provenance"]
    return facts


def v5_edges(
    g: ProjectGraph,
    keep_types: Set[str],
    keep_methods: Set[str],
    keep_fields: Set[str],
) -> List[dict]:
    kept = keep_types | keep_methods | keep_fields
    bucket: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    meta: Dict[Tuple[str, str], dict] = defaultdict(
        lambda: {"counts": {}, "provenance": None, "via": None, "via_kind": None}
    )

    def add(
        src: str,
        dst: str,
        rel: str,
        *,
        count: int = 1,
        provenance: Optional[str] = None,
        via: Optional[str] = None,
        via_kind: Optional[str] = None,
    ) -> None:
        if not (src in kept and dst in kept and src and dst and rel):
            return
        bucket[(src, dst)].add(rel)
        slot = meta[(src, dst)]
        if rel in _BEHAVIOR:
            slot["counts"][rel] = slot["counts"].get(rel, 0) + max(count, 1)
        if provenance:
            old = slot["provenance"]
            if old is None or _PROVENANCE_RANK.get(provenance, 0) >= _PROVENANCE_RANK.get(old, 0):
                slot["provenance"] = provenance
        if via and via_kind and not slot["via"]:
            slot["via"] = via
            slot["via_kind"] = via_kind

    def bind_call(src: str, dst: str) -> Optional[str]:
        """Project callee. A library receiver, or a call that lands on ``src``, is dropped."""
        resolved = resolve_callee(g, dst, None)
        if not resolved or resolved == src:
            return None
        return resolved

    for e in g.edges:
        if e.type in IGNORE_SGR:
            continue
        src, dst = e.source, e.target

        if e.type in SGR_TO_V5 and e.type not in {"HAS_TYPE", "DELEGATES_VIA"}:
            if e.type == "CALLS":
                dst2 = bind_call(src, dst)
                if dst2:
                    add(src, dst2, SGR_TO_V5[e.type], count=e.count)
            elif e.type in {"OVERRIDES", "IMPLEMENTS_METHOD"}:
                dst2 = resolve_callee(g, dst, None) or dst
                add(src, dst2, SGR_TO_V5[e.type], count=e.count)
            elif e.type in {"READS_FIELD", "WRITES_FIELD"}:
                add(
                    src,
                    dst,
                    SGR_TO_V5[e.type],
                    count=e.count,
                    provenance=e.provenance if e.type == "WRITES_FIELD" else None,
                )
            else:
                add(src, dst, SGR_TO_V5[e.type], count=e.count)
            continue

        if e.type == "HAS_TYPE":
            if g.is_field(src):
                if dst in keep_types and not g.is_jdk_collection_type(dst):
                    add(src, dst, "has")
            continue

        if e.type == "DELEGATES_VIA":
            dst2 = bind_call(src, dst)
            if dst2:
                via_name = g.simple_name(e.via) if e.via else None
                add(
                    src,
                    dst2,
                    "delegates",
                    count=e.count,
                    via=via_name,
                    via_kind=e.via_kind,
                )
            if e.via_kind == "field" and e.via:
                add(src, e.via, "delegates_via_field", count=e.count)

    # Parameter HAS_TYPE -> method references (method owns PR via HAS_PARAMETER)
    for mid, prs in g.params.items():
        for pr in prs:
            t = g.has_type.get(pr)
            if t:
                add(mid, t, "references")

    # Inferred has_element (raw Vector etc.)
    for fid in keep_fields:
        for t in infer_element_types(g, fid):
            add(fid, t, "has_element")

    edges = []
    for (src, dst), rels in sorted(bucket.items()):
        edge = {
            "src_id": src,
            "dst_id": dst,
            "relation_type": _relation_list(rels),
        }
        facts = _edge_facts(rels, meta[(src, dst)])
        if facts:
            edge["facts"] = facts
        edges.append(edge)
    assert_no_call_self_loops(edges)
    return edges


def type_adjacency(edges: List[dict], g: ProjectGraph, keep_types: Set[str]) -> Dict[str, Set[str]]:
    adj: Dict[str, Set[str]] = defaultdict(set)

    def owner(nid: str) -> Optional[str]:
        if nid in keep_types:
            return nid
        return g.owner_type(nid)

    type_type_rels = {
        "inherits",
        "implements",
        "has",
        "has_element",
        "uses",
        "uses_element",
        "references",
        "creates",
    }
    method_method = {"calls", "delegates", "overrides", "implements_method"}

    for e in edges:
        rels = set(e["relation_type"])
        s, d = e["src_id"], e["dst_id"]
        so, do = owner(s), owner(d)
        if rels & type_type_rels and so and do and so in keep_types and do in keep_types:
            adj[so].add(do)
            adj[do].add(so)
        if rels & method_method and so and do and so in keep_types and do in keep_types:
            adj[so].add(do)
            adj[do].add(so)
    return adj


def structural_features(
    g: ProjectGraph,
    gold: str,
    keep_types: Set[str],
    keep_methods: Set[str],
    keep_fields: Set[str],
    edges: List[dict],
) -> Dict[str, dict]:
    adj = type_adjacency(edges, g, keep_types)
    dist = {gold: 0.0}
    q = deque([gold])
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1.0
                q.append(v)

    # Bidirectional method calls across types
    calls_out: Dict[str, Set[str]] = defaultdict(set)
    for e in edges:
        if "calls" not in e["relation_type"] and "delegates" not in e["relation_type"]:
            continue
        s, d = e["src_id"], e["dst_id"]
        if g.is_method(s) and g.is_method(d):
            so, do = g.owner_type(s), g.owner_type(d)
            if so and do and so != do:
                calls_out[s].add(d)

    bidir_methods: Set[str] = set()
    for s, tgts in calls_out.items():
        so = g.owner_type(s)
        for d in tgts:
            do = g.owner_type(d)
            # some method of do calls back to so
            for m2 in g.methods_of(do or ""):
                if m2 not in keep_methods:
                    continue
                for back in calls_out.get(m2, ()):
                    if g.owner_type(back) == so:
                        bidir_methods.add(s)
                        bidir_methods.add(m2)

    bidir_types: Set[str] = {g.owner_type(m) for m in bidir_methods if g.owner_type(m)}

    feats = {}
    for nid in keep_types | keep_methods | keep_fields:
        if nid in keep_types:
            hop = dist.get(nid)
            if hop is None:
                hop = -1.0
            feats[nid] = {
                "is_root": 1.0 if nid == gold else 0.0,
                "hop_distance": float(hop),
                "has_bidirectional_call": 1.0 if nid in bidir_types else 0.0,
            }
        else:
            owner = g.owner_type(nid)
            hop = dist.get(owner, -1.0) if owner else -1.0
            feats[nid] = {
                "is_root": 0.0,
                "hop_distance": float(hop),
                "has_bidirectional_call": 1.0 if nid in bidir_methods else 0.0,
            }
    return feats
