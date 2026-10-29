from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from annotation_io import catalogue_lines
from audit_spines import collect_audit_rows
from constants import GOLD_CSV_NAME
from paths import DATASET, GOLD_CSV, LOGS_DIR, SPINE_OVERLAY, SGR_ROOT

OVERLAY = SPINE_OVERLAY
REPORT = LOGS_DIR / "stamp_clean.csv"

MIN_NODES = 3
NOISE_DROP = frozenset({"empty", "test_root", "sample_pkg", "test_or_sample_type"})


def drop_reasons(row: dict) -> List[str]:
    reasons: List[str] = []
    if row["bucket"] != "PASS":
        reasons.append(row["bucket"])
    if row["n"] < MIN_NODES:
        reasons.append("tiny")
    for flag in row["noise"]:
        if flag in NOISE_DROP or flag.startswith("multi_label"):
            reasons.append(flag)
    return reasons


def write_overlay(
    source: Path, dest: Path, line_active: Dict[int, bool]
) -> Tuple[int, int]:
    """v5 worklist: only spine-clean rows stay active. Unconverted gold → 0."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    flipped = 0
    keep_lines = 0
    with source.open(newline="") as inf, dest.open("w", newline="") as outf:
        reader = csv.DictReader(inf)
        if not reader.fieldnames:
            raise ValueError(f"empty gold CSV: {source}")
        writer = csv.DictWriter(outf, fieldnames=list(reader.fieldnames))
        writer.writeheader()
        for i, row in enumerate(reader, start=2):
            keep = bool(line_active.get(i, False))
            new = "1" if keep else "0"
            if keep:
                keep_lines += 1
            if str(row.get("active", "")).strip() != new:
                flipped += 1
            row["active"] = new
            writer.writerow(row)
    return keep_lines, flipped


def write_report(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "keep",
        "reasons",
        "bucket",
        "label",
        "n",
        "folder",
        "name",
        "csv_lines",
        "j",
        "s",
        "noise",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "keep": "1" if r["keep"] else "0",
                    "reasons": "|".join(r["reasons"]),
                    "bucket": r["bucket"],
                    "label": r["label"],
                    "n": r["n"],
                    "folder": r["folder"],
                    "name": r["name"],
                    "csv_lines": "|".join(str(x) for x in r["csv_lines"]),
                    "j": r["j"],
                    "s": r["s"],
                    "noise": "|".join(r["noise"]),
                }
            )


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Stamp v5 gold.active from spine audit")
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--graph-dpd", "--sgr-root", dest="graph_dpd", type=Path, default=SGR_ROOT)
    p.add_argument("--gold", type=Path, default=GOLD_CSV)
    p.add_argument("--overlay", type=Path, default=OVERLAY)
    p.add_argument("--report", type=Path, default=REPORT)
    p.add_argument("--project", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    rows = collect_audit_rows(args.dataset, args.graph_dpd, args.project)
    if not rows:
        print("no annotation.json", file=sys.stderr)
        return 1

    line_active: Dict[int, bool] = {}
    keep_by_lab: Counter = Counter()
    drop_by_lab: Counter = Counter()
    drop_why: Counter = Counter()
    stamped: List[dict] = []

    for row in rows:
        if not row["csv_lines"] and row.get("file_path"):
            row["csv_lines"] = catalogue_lines(
                args.gold, row.get("project_name") or "", row["file_path"]
            )
        reasons = drop_reasons(row)
        keep = not reasons
        row["keep"] = keep
        row["reasons"] = reasons
        stamped.append(row)
        if keep:
            keep_by_lab[row["label"]] += 1
        else:
            drop_by_lab[row["label"]] += 1
            for why in reasons:
                drop_why[why] += 1
        for line in row["csv_lines"]:
            line_active[int(line)] = keep
        if args.dry_run:
            continue
        # The annotation gold object stays file_path and project_name.
        # Activity is recorded on the overlay CSV, not in the project file.

    write_report(args.report, stamped)
    overlay_n = overlay_flipped = 0
    if not args.dry_run:
        overlay_n, overlay_flipped = write_overlay(args.gold, args.overlay, line_active)

    print(
        f"{'dry-run ' if args.dry_run else ''}graphs={len(rows)} "
        f"keep={sum(keep_by_lab.values())} drop={sum(drop_by_lab.values())}"
    )
    print()
    print("## keep by label")
    for lab in sorted(set(keep_by_lab) | set(drop_by_lab)):
        print(f"  {lab:16} keep={keep_by_lab[lab]:3}  drop={drop_by_lab[lab]:3}")
    print()
    print("## drop reasons (a graph can have several)")
    for why, n in drop_why.most_common():
        print(f"  {why:24} {n}")
    print()
    print(f"report {args.report}")
    if args.dry_run:
        print("no JSON / overlay written")
    else:
        print(f"overlay {args.overlay}  stamped_csv_lines={overlay_n} flipped={overlay_flipped}")
        print(f"catalogue gold unchanged: {args.gold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
