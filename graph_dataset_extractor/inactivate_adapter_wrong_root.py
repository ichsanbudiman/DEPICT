"""Inactivate checked Adapter gold whose root is the wrong role.

Reads gold_adapter_wrong_root.txt. Sets JSON gold.active=false. Does not rename
files, does not edit the v4 gold CSV, does not change label.

  python3 inactivate_adapter_wrong_root.py
  python3 inactivate_adapter_wrong_root.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import List, Optional, Tuple

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from annotation_io import catalogue_lines, find_instance, instance_label, load_document, write_document
from constants import GOLD_CSV_NAME
from paths import DATASET, GOLD_ADAPTER_WRONG_ROOT, GOLD_CSV, SPINE_OVERLAY

LIST_PATH = GOLD_ADAPTER_WRONG_ROOT
OVERLAY = SPINE_OVERLAY
REPO_GOLD = GOLD_CSV
REASON = "wrong_adapter_root"
EXPECTED = 8


def load_list(path: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        folder, stem = line.split("\t", 1)
        rows.append((folder.strip(), stem.strip()))
    return rows


def patch_instance(inst: dict) -> str:
    before = instance_label(inst)
    if before != "Adapter":
        raise SystemExit(f"{inst.get('id')}: label {before!r} is not Adapter")
    return before


def instance_lines(inst: dict) -> List[int]:
    gold = inst.get("gold") or {}
    lines = list(gold.get("csv_lines") or [])
    if lines:
        return [int(x) for x in lines]
    return catalogue_lines(REPO_GOLD, str(gold.get("project_name") or ""), str(gold.get("file_path") or ""))


def overlay_off(csv_lines: List[int], dry: bool) -> int:
    if not OVERLAY.is_file() or not csv_lines:
        return 0
    want = {int(x) for x in csv_lines}
    with OVERLAY.open(newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    n = 0
    for i, row in enumerate(rows, start=2):
        if i in want and str(row.get("active", "1")) != "0":
            row["active"] = "0"
            n += 1
    if not dry and n:
        with OVERLAY.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return n


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Inactivate wrong-root Adapter gold")
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--list", type=Path, default=LIST_PATH)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    items = load_list(args.list)
    if len(items) != EXPECTED:
        print(f"expected {EXPECTED} stems, got {len(items)}", file=sys.stderr)
        return 1

    changed = 0
    overlay_n = 0
    by_folder: dict = defaultdict(list)
    for folder, stem in items:
        by_folder[folder].append(stem)
    for folder, stems in by_folder.items():
        path = args.dataset / folder / "annotation.json"
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            return 1
        doc = load_document(path)
        for stem in stems:
            inst = find_instance(doc, stem)
            lines = instance_lines(inst)
            patch_instance(inst)
            overlay_n += overlay_off(lines, args.dry_run)
            changed += 1
            print(f"  {folder}/{stem}  Adapter active → false")
        if not args.dry_run:
            write_document(path, doc)

    print(
        f"{'dry-run ' if args.dry_run else ''}inactivated={changed}  "
            f"overlay_rows={overlay_n}  catalogue gold unchanged: {REPO_GOLD}"
    )
    print("re-run after stamp. regenerate folds next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
