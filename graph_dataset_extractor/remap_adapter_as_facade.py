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

from annotation_io import (
    catalogue_lines,
    find_instance,
    instance_label,
    load_document,
    write_document,
)
from constants import GOLD_CSV_NAME
from paths import DATASET, GOLD_ADAPTER_AS_FACADE, GOLD_CSV, SPINE_OVERLAY

LIST_PATH = GOLD_ADAPTER_AS_FACADE
OVERLAY = SPINE_OVERLAY
REPO_GOLD = GOLD_CSV
REMAP = "Adapter_as_Facade"


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
    if before not in ("Adapter", "Facade"):
        raise SystemExit(f"{inst.get('id')}: label {before!r} is not Adapter/Facade")
    inst["proposed"] = ["Facade"]
    return before


def instance_lines(inst: dict) -> List[int]:
    gold = inst.get("gold") or {}
    lines = list(gold.get("csv_lines") or [])
    if lines:
        return [int(x) for x in lines]
    return catalogue_lines(REPO_GOLD, str(gold.get("project_name") or ""), str(gold.get("file_path") or ""))


def overlay_lines(csv_lines: List[int], dry: bool, *, to_label: str, from_label: str) -> int:
    if not OVERLAY.is_file() or not csv_lines:
        return 0
    want = {int(x) for x in csv_lines}
    with OVERLAY.open(newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    n = 0
    for i, row in enumerate(rows, start=2):
        if i in want and row.get("label") == from_label:
            row["label"] = to_label
            n += 1
    if not dry and n:
        with OVERLAY.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return n


def restore_instance(inst: dict) -> str:
    before = instance_label(inst)
    inst["proposed"] = ["Adapter"]
    return before


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Adapter→Facade remap (20 locked stems)")
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--list", type=Path, default=LIST_PATH)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--undo", action="store_true", help="restore Adapter labels")
    p.add_argument("--apply", action="store_true", help="apply gold_adapter_as_facade.txt")
    args = p.parse_args(argv)
    if args.apply and args.undo:
        print("use only one of --undo / --apply", file=sys.stderr)
        return 1
    if not args.apply and not args.undo:
        args.apply = True

    items = load_list(args.list)
    if len(items) != 20:
        print(f"expected 20 stems, got {len(items)}", file=sys.stderr)
        return 1
    if args.undo:
        overlay_n = 0
        undone = 0
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
                before = restore_instance(inst)
                overlay_n += overlay_lines(lines, args.dry_run, to_label="Adapter", from_label="Facade")
                undone += 1
                print(f"  {folder}/{stem}  {before} → Adapter")
            if not args.dry_run:
                write_document(path, doc)
        print(
            f"{'dry-run ' if args.dry_run else ''}undone={undone}  "
            f"overlay_rows={overlay_n}  catalogue gold unchanged: {REPO_GOLD}"
        )
        print("regenerate folds next.")
        return 0

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
            before = patch_instance(inst)
            lines = instance_lines(inst)
            overlay_n += overlay_lines(lines, args.dry_run, to_label="Facade", from_label="Adapter")
            changed += 1
            print(f"  {folder}/{stem}  {before} → Facade")
        if not args.dry_run:
            write_document(path, doc)

    print(
        f"{'dry-run ' if args.dry_run else ''}remapped={changed}  "
        f"overlay_rows={overlay_n}  catalogue gold unchanged: {REPO_GOLD}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
