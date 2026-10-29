"""Three-bucket spine audit (docs/pattern_spines.md). Label is audit-only.

  PASS          spine present in pruned JSON
  KEEPSET_DROP  spine in project_sgr.yaml, missing in JSON
  SGR_MISS      spine never in project.yaml

Gold-noise flags are orthogonal (test root, multi-label, sample package).
Do not invent edges. Convert stays label-free.

  python3 audit_spines.py
  python3 audit_spines.py --project JUnit_v3.7
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from annotation_io import iter_instances, pruned_view
from keepset import (
    ancestor_types,
    descendant_types,
    field_collab_types,
    infer_element_types,
    method_ref_types,
    method_use_types,
)
from sgr_load import ProjectGraph, load_project_yaml
from paths import DATASET, GRAPH_FILE, PROJECT_SGR_FILE, SAMPLE, SGR_ROOT, V5_ROOT

FACTORY = {"FactoryMethod", "AbstractFactory"}


def brief(nid: str) -> str:
    return nid.split(":", 1)[-1]


def json_owner(mid: str, types: Set[str]) -> Optional[str]:
    best = None
    for t in types:
        if mid.startswith(t) or mid.startswith(t.split(":", 1)[-1]):
            if best is None or len(t) > len(best):
                best = t
    if best:
        return best
    # Graph-DPD method id is usually M:pkg.Type.method — match type suffix
    tail = brief(mid)
    for t in types:
        tn = brief(t)
        if tail.startswith(tn + ".") or tail.startswith(tn + "#"):
            return t
    return None


def load_pruned(path: Path) -> dict:
    return json.loads(path.read_text())


def graph_index(g: dict) -> Tuple[Set[str], Set[str], Set[str], Dict[str, Set[str]]]:
    types, methods, fields = set(), set(), set()
    rel: Dict[str, Set[str]] = defaultdict(set)
    for n in g["nodes"]:
        if n["kind"] == "type":
            types.add(n["id"])
        elif n["kind"] == "method":
            methods.add(n["id"])
        elif n["kind"] == "field":
            fields.add(n["id"])
    for e in g["edges"]:
        for r in e["relation_type"]:
            rel[(e["src_id"], e["dst_id"])].add(r)
    return types, methods, fields, rel


def rels_from(rel, src, name) -> List[str]:
    return [d for (s, d), rs in rel.items() if s == src and name in rs]


def has_rel(rel, src, dst, name) -> bool:
    return name in rel.get((src, dst), ())


# ----- JSON spine -----


def adapter_json(gold, types, methods, fields, rel) -> Tuple[bool, str]:
    gold_fields = [f for f in fields if json_owner(f, {gold}) == gold or gold.split(":", 1)[-1] in f]
    slot_has = False
    for f in gold_fields:
        for t in rels_from(rel, f, "has"):
            if t != gold and t in types:
                slot_has = True
    inherits = any(has_rel(rel, gold, t, "inherits") for t in types if t != gold)
    delegates = False
    for m in methods:
        if json_owner(m, {gold}) != gold and gold.split(":", 1)[-1] not in m:
            continue
        if rels_from(rel, m, "delegates") or rels_from(rel, m, "delegates_via_field"):
            delegates = True
    if delegates and (slot_has or inherits):
        return True, "delegates + " + ("slot" if slot_has else "inherits")
    if delegates and not slot_has and not inherits:
        return False, "delegates but no slot has / inherit (object adapter incomplete)"
    if slot_has and not delegates:
        return False, "slot has but no delegates"
    return False, "no delegates / slot"


def facade_json(gold, types, methods, fields, rel) -> Tuple[bool, str]:
    callee_owners: Set[str] = set()
    callee_methods: Set[str] = set()
    gold_ms = [m for m in methods if gold.split(":", 1)[-1] in m]
    for m in gold_ms:
        for d in rels_from(rel, m, "calls"):
            if d in methods:
                callee_methods.add(d)
                own = json_owner(d, types)
                if own and own != gold:
                    callee_owners.add(own)
    has_el = False
    for f in fields:
        if gold.split(":", 1)[-1] in f and rels_from(rel, f, "has_element"):
            has_el = True
    if len(callee_owners) >= 2:
        return True, f"calls {len(callee_owners)} subsystem types"
    if has_el and len(callee_methods) >= 2:
        # workers present; calls may be util-only
        if len(callee_owners) <= 1:
            return False, "has_element workers but calls only 1 owner (often forEach util)"
        return True, "has_element + multi calls"
    if has_el:
        return False, "has_element but no worker-method calls"
    return False, f"calls {len(callee_owners)} subsystem types"


def observer_json(gold, types, methods, fields, rel) -> Tuple[bool, str]:
    gold_fields = [f for f in fields if gold.split(":", 1)[-1] in f]
    listeners = []
    for f in gold_fields:
        for t in rels_from(rel, f, "has_element"):
            if t in types:
                listeners.append((f, t))
    if not listeners:
        return False, "no gold has_element"
    attach = False
    notify = False
    incoming = False
    for f, lis in listeners:
        for m in methods:
            if has_rel(rel, m, f, "writes_field") and gold.split(":", 1)[-1] in m:
                attach = True
        for m in methods:
            if gold.split(":", 1)[-1] not in m:
                continue
            for d in rels_from(rel, m, "calls"):
                if lis.split(":", 1)[-1] in d or json_owner(d, types) == lis:
                    notify = True
        for m in methods:
            if gold.split(":", 1)[-1] in m and any(
                has_rel(rel, m, f, x) for x in ("writes_field", "delegates_via_field")
            ):
                for src, dst in rel:
                    if dst == m and "calls" in rel[(src, dst)] and gold.split(":", 1)[-1] not in src:
                        incoming = True
    bits = []
    if attach:
        bits.append("attach")
    if notify:
        bits.append("notify")
    if incoming:
        bits.append("incoming")
    if listeners and notify:
        return True, "has_element + " + ",".join(bits)
    return False, "has_element but no notify calls to element type (" + ",".join(bits or ["none"]) + ")"


def _json_closure(start: Set[str], types: Set[str], rel, outgoing: bool) -> Set[str]:
    seen = set(start)
    stack = list(start)
    while stack:
        u = stack.pop()
        for t in types:
            if t in seen:
                continue
            hit = (
                has_rel(rel, u, t, "inherits") or has_rel(rel, u, t, "implements")
                if outgoing
                else has_rel(rel, t, u, "inherits") or has_rel(rel, t, u, "implements")
            )
            if hit:
                seen.add(t)
                stack.append(t)
    return seen


def visitor_json(gold, types, methods, fields, rel) -> Tuple[bool, str]:
    gold_ms = [m for m in methods if gold.split(":", 1)[-1] in m]
    refs = []
    for m in gold_ms:
        for t in rels_from(rel, m, "references"):
            if t in types and t != gold:
                refs.append((m, t))
    if not refs:
        return False, "visit has no references"
    family = _json_closure({gold}, types, rel, outgoing=True)
    elem_family = _json_closure({t for _, t in refs}, types, rel, outgoing=False)
    visit_ms = {m for m, _ in refs}
    iface_ms = set()
    for m in visit_ms:
        iface_ms.update(rels_from(rel, m, "implements_method"))
        iface_ms.update(rels_from(rel, m, "overrides"))
    visit_tgts = visit_ms | iface_ms
    accept_calls = False
    typed_close = False
    for am in methods:
        owner = json_owner(am, types)
        if owner == gold or (owner is None and gold.split(":", 1)[-1] in am):
            continue
        if owner not in elem_family and not any(
            brief(t) in am or t.split(":", 1)[-1] in am for t in elem_family
        ):
            continue
        if any(t in family for t in rels_from(rel, am, "references")):
            typed_close = True
        for d in list(rels_from(rel, am, "calls")) + list(rels_from(rel, am, "delegates")):
            if d in visit_tgts:
                accept_calls = True
    if accept_calls:
        return True, f"visit references {len(refs)} + accept→visit"
    if typed_close:
        return True, f"visit references {len(refs)} + accept references visitor"
    return False, "visit references but no accept→visit"


def _not_self(gold: str, types: List[str]) -> List[str]:
    return [t for t in types if t != gold]


def factory_json(gold, types, methods, fields, rel) -> Tuple[bool, str]:
    gold_ms = [m for m in methods if gold.split(":", 1)[-1] in m]
    paired = 0
    uses_only = 0
    for m in gold_ms:
        uses = _not_self(
            gold, [t for t in rels_from(rel, m, "uses") + rels_from(rel, m, "uses_element") if t in types]
        )
        creates = _not_self(gold, [t for t in rels_from(rel, m, "creates") if t in types])
        if uses and creates:
            paired += 1
        elif uses:
            uses_only += 1
    # subtype override creates (not singleton self-new)
    sub_creates = 0
    for m in methods:
        if gold.split(":", 1)[-1] in m:
            continue
        if rels_from(rel, m, "overrides") or rels_from(rel, m, "implements_method"):
            uses = _not_self(
                gold, rels_from(rel, m, "uses") + rels_from(rel, m, "uses_element")
            )
            creates = _not_self(gold, rels_from(rel, m, "creates"))
            if uses and creates:
                sub_creates += 1
    if paired:
        return True, f"{paired} gold uses+creates"
    if uses_only and sub_creates:
        return True, f"abstract uses + {sub_creates} subtype creates"
    if uses_only:
        return False, f"uses product but no creates (gold or subtype)"
    if sub_creates:
        return True, "subtype uses+creates only"
    return False, "no uses+creates"


# ----- SGR spine -----


def adapter_sgr(g: ProjectGraph, gold: str) -> Tuple[bool, str]:
    slot = False
    for fid in g.fields_of(gold):
        if any(t != gold for t in field_collab_types(g, fid)):
            slot = True
    inherits = any(
        e.type == "EXTENDS" and g.is_type(e.target) for e in g.out.get(gold, ())
    )
    delegates = False
    for mid in g.methods_of(gold):
        for e in g.out.get(mid, ()):
            if e.type == "DELEGATES_VIA":
                delegates = True
            if e.type == "CALLS" and inherits:
                delegates = True
    if delegates and (slot or inherits):
        return True, "SGR delegates + slot/inherit"
    if delegates:
        return False, "SGR delegates, no slot/inherit"
    if slot:
        return False, "SGR slot, no delegates"
    return False, "SGR no adapter edges"


def facade_sgr(g: ProjectGraph, gold: str) -> Tuple[bool, str]:
    owners: Set[str] = set()
    for mid in g.methods_of(gold):
        for e in g.out.get(mid, ()):
            if e.type != "CALLS":
                continue
            tgt = e.target
            if g.is_method(tgt) and not g.is_external_method(tgt):
                own = g.owner_type(tgt)
                if own and own != gold and g.is_type(own):
                    owners.add(own)
    has_el = any(infer_element_types(g, fid) for fid in g.fields_of(gold))
    if len(owners) >= 2:
        return True, f"SGR calls {len(owners)} types"
    if has_el:
        return False, f"SGR has_element, calls {len(owners)} project owners"
    return False, f"SGR calls {len(owners)} project owners"


def observer_sgr(g: ProjectGraph, gold: str) -> Tuple[bool, str]:
    elems = set()
    coll_fields = []
    for fid in g.fields_of(gold):
        ets = infer_element_types(g, fid)
        if ets:
            coll_fields.append(fid)
            elems |= ets
    if not elems:
        return False, "SGR no has_element"
    notify = False
    for mid in g.methods_of(gold):
        for e in g.out.get(mid, ()):
            if e.type != "CALLS":
                continue
            tgt = e.target
            own = g.owner_type(tgt) if g.is_method(tgt) else None
            if own in elems:
                notify = True
            # EM resolve by name onto listener
            name = g.simple_name(tgt)
            for lm in g.methods_named(name):
                if g.owner_type(lm) in elems:
                    notify = True
    attach = False
    for fid in coll_fields:
        if g.field_writers.get(fid) or g.field_delegators.get(fid):
            attach = True
    if notify:
        return True, "SGR has_element + notify" + (" + attach" if attach else "")
    return False, "SGR has_element, no notify CALLS to element"


def visitor_sgr(g: ProjectGraph, gold: str) -> Tuple[bool, str]:
    refs = set()
    for mid in g.methods_of(gold):
        refs |= {t for t in method_ref_types(g, mid) if t != gold}
    if not refs:
        return False, "SGR visit no references"
    family = {gold} | ancestor_types(g, gold)
    gold_ms = set(g.methods_of(gold))
    iface_ms = set()
    for mid in gold_ms:
        for e in g.out.get(mid, ()):
            if e.type in {"IMPLEMENTS_METHOD", "OVERRIDES"}:
                iface_ms.add(e.target)
    accept = False
    for t in refs:
        for t2 in {t} | descendant_types(g, t):
            for mid in g.methods_of(t2):
                for e in g.out.get(mid, ()):
                    if e.type in {"CALLS", "DELEGATES_VIA"} and (
                        e.target in gold_ms or e.target in iface_ms
                    ):
                        accept = True
                if method_ref_types(g, mid) & family:
                    accept = True  # typed close possible
    if accept:
        return True, "SGR references + accept/typed-close"
    return False, "SGR references, no accept CALLS or Visitor param"


def factory_sgr(g: ProjectGraph, gold: str) -> Tuple[bool, str]:
    paired = 0
    uses_only = 0
    for mid in g.methods_of(gold):
        uses = {t for t in method_use_types(g, mid) if t != gold}
        creates = {
            e.target
            for e in g.out.get(mid, ())
            if e.type == "CREATES" and g.is_type(e.target) and e.target != gold
        }
        if uses and creates:
            paired += 1
        elif uses:
            uses_only += 1
    sub = 0
    for e in g.inn.get(gold, ()):
        if e.type not in {"EXTENDS", "IMPLEMENTS"}:
            continue
        sub_t = e.source
        if not g.is_type(sub_t):
            continue
        for mid in g.methods_of(sub_t):
            uses = {t for t in method_use_types(g, mid) if t != gold and t != sub_t}
            creates = {
                x.target
                for x in g.out.get(mid, ())
                if x.type == "CREATES"
                and g.is_type(x.target)
                and x.target not in {gold, sub_t}
            }
            if uses and creates:
                sub += 1
    if paired:
        return True, f"SGR {paired} uses+creates"
    if uses_only and sub:
        return True, "SGR abstract uses + subtype creates"
    if uses_only:
        return False, "SGR uses, no creates"
    return False, "SGR no uses+creates"


def none_json(gold, types, methods, fields, rel) -> Tuple[bool, str]:
    leaks = []
    for lab, (jfn, _) in (
        ("Adapter", (adapter_json, None)),
        ("Facade", (facade_json, None)),
        ("Observer", (observer_json, None)),
        ("Visitor", (visitor_json, None)),
        ("Factory", (factory_json, None)),
    ):
        ok, why = jfn(gold, types, methods, fields, rel)
        if ok:
            leaks.append(f"{lab}:{why}")
    n = len(types) + len(methods) + len(fields)
    if leaks:
        return False, "leaks " + "; ".join(leaks)
    if n <= 1:
        return True, "empty None"
    return True, "no pattern spine"


def none_sgr(g: ProjectGraph, gold: str) -> Tuple[bool, str]:
    return True, "None has no required spine"


CHECKERS = {
    "Adapter": (adapter_json, adapter_sgr),
    "Facade": (facade_json, facade_sgr),
    "Observer": (observer_json, observer_sgr),
    "Visitor": (visitor_json, visitor_sgr),
    "FactoryMethod": (factory_json, factory_sgr),
    "AbstractFactory": (factory_json, factory_sgr),
    "None": (none_json, none_sgr),
}


def gold_noise_flags(gjson: dict, sgr: ProjectGraph) -> List[str]:
    flags = []
    gold = gjson["root"]
    fp = gjson.get("gold", {}).get("file_path", "")
    low = fp.lower()
    name = brief(gold).rsplit(".", 1)[-1]
    if "/test/" in low or "/tests/" in low or name.endswith("Test") or name.endswith("Tests"):
        flags.append("test_root")
    if "/samples/" in low or "/sample/" in low:
        flags.append("sample_pkg")
    n = sgr.node(gold)
    if n:
        qn = (n.qualified_name or "").lower()
        pkg = qn.rsplit(".", 1)[0] if "." in qn else ""
        if set(pkg.split(".")) & {"test", "tests", "samples", "sample"}:
            flags.append("test_or_sample_type")
    return flags


def project_yaml_path(graph_dpd: Path, folder: str) -> Path:
    yp = graph_dpd / folder / PROJECT_SGR_FILE
    if yp.is_file():
        return yp
    return graph_dpd / folder.replace("_(", "_").replace(")", "") / PROJECT_SGR_FILE


def classify_graph(
    gjson: dict, sgr: ProjectGraph, sibling_labels: List[str]
) -> dict:
    label = gjson["label"]
    gold = gjson["root"]
    types, methods, fields, rel = graph_index(gjson)
    jfn, sfn = CHECKERS[label]
    j_ok, j_why = jfn(gold, types, methods, fields, rel)
    s_ok, s_why = sfn(sgr, gold)
    if label == "None" and not j_ok:
        bucket = "NONE_LEAK"
    elif j_ok:
        bucket = "PASS"
    elif s_ok:
        bucket = "KEEPSET_DROP"
    else:
        bucket = "SGR_MISS"
    noise = gold_noise_flags(gjson, sgr)
    if len(set(sibling_labels)) > 1:
        noise.append("multi_label:" + "+".join(sorted(set(sibling_labels))))
    if len(gjson["nodes"]) <= 1:
        noise.append("empty")
    return {
        "bucket": bucket,
        "label": label,
        "n": len(gjson["nodes"]),
        "name": brief(gold),
        "j": j_why,
        "s": s_why,
        "noise": noise,
        "csv_lines": list(gjson.get("gold", {}).get("csv_lines") or []),
        "root": gold,
        "project_name": gjson.get("gold", {}).get("project_name", ""),
    }


def collect_audit_rows(
    dataset: Path, graph_dpd: Path, project: Optional[str] = None
) -> List[dict]:
    by_folder: Dict[str, List[Tuple[Path, dict, dict]]] = defaultdict(list)
    multi: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for path, folder, inst in iter_instances(dataset, project):
        gjson = pruned_view(inst)
        by_folder[folder].append((path, inst, gjson))
        multi[(gjson["gold"]["project_name"], gjson["root"])].append(gjson["label"])

    rows: List[dict] = []
    for folder, items in sorted(by_folder.items()):
        yp = project_yaml_path(graph_dpd, folder)
        print(f"load {yp}  graphs={len(items)}", file=sys.stderr)
        if not yp.is_file():
            print(f"missing project.yaml: {folder}", file=sys.stderr)
            continue
        sgr = load_project_yaml(yp)
        for path, inst, gjson in items:
            rec = classify_graph(
                gjson, sgr, multi[(gjson["gold"]["project_name"], gjson["root"])]
            )
            rec["folder"] = folder
            rec["path"] = path
            rec["instance_id"] = inst.get("id")
            rec["file_path"] = (inst.get("gold") or {}).get("file_path", "")
            rows.append(rec)
        del sgr
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="v5 spine audit")
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--graph-dpd", "--sgr-root", dest="graph_dpd", type=Path, default=SGR_ROOT)
    p.add_argument("--project", default=None)
    args = p.parse_args(argv)

    rows = collect_audit_rows(args.dataset, args.graph_dpd, args.project)
    if not rows:
        print(f"no {GRAPH_FILE}", file=sys.stderr)
        return 1

    buckets = Counter(r["bucket"] for r in rows)

    print(f"graphs={len(rows)}  {dict(buckets)}")
    print()
    for b in ("KEEPSET_DROP", "NONE_LEAK", "SGR_MISS", "PASS"):
        subset = [r for r in rows if r["bucket"] == b]
        print(f"## {b} ({len(subset)})")
        show = subset if b != "PASS" else [r for r in subset if r["noise"] or r["n"] <= 2]
        if b == "PASS":
            print(f"  (listing only noisy/tiny PASS; silent PASS={len(subset)-len(show)})")
        for r in sorted(show, key=lambda x: (x["label"], -x["n"], x["name"])):
            nz = f"  [{', '.join(r['noise'])}]" if r["noise"] else ""
            print(
                f"  {r['label']:16} n={r['n']:4}  {r['folder']}  {r['name']}"
                f"  json={r['j']}  sgr={r['s']}{nz}"
            )
        print()
    by_lab = defaultdict(Counter)
    for r in rows:
        by_lab[r["label"]][r["bucket"]] += 1
    print("## by label")
    for lab, c in sorted(by_lab.items()):
        print(f"  {lab:16} {dict(c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
