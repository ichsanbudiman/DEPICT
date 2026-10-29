"""Extract SGR only for gold projects that have an active row.

Writes each project under ``SGR_ROOT`` (``dataset/``)::

    dataset/<Project>/
      metadata.yaml
      project_sgr.yaml
      full_project_graph.yaml
      files/
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from paths import DEPICT_ROOT, GOLD_CSV, JAVA_PROJECTS, PROJECT_SGR_FILE, SGR_ROOT, ensure_sgr_on_path

SUMMARY_LOG = DEPICT_ROOT / "logs" / "extract_sgr_summary.log"


def folder_name(project_name: str) -> str:
    return project_name.replace(" ", "_")


def is_active(value: str) -> bool:
    return str(value).strip() in {"1", "true", "True"}


def load_active_projects(gold_csv: Path) -> List[Tuple[str, str]]:
    """Unique project names that have at least one active row.

    The second value is a file_path prefix from an active row, used to find
    the Java directory when it differs from the project name.
    """
    found: Dict[str, str] = {}
    with gold_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            if not is_active(row.get("active", "")):
                continue
            name = row["project_name"]
            if name in found:
                continue
            path = row.get("file_path") or ""
            found[name] = path.split("/", 1)[0]
    return list(found.items())


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip())
    return cleaned.strip("_") or "project"


def java_dir_candidates(project_name: str, path_prefix: str) -> List[str]:
    names: List[str] = []

    def add(name: str) -> None:
        name = name.strip().strip("/")
        if name and name not in names:
            names.append(name)

    add(project_name)
    add(path_prefix)
    add(folder_name(project_name))
    add(folder_name(path_prefix))
    add(_safe_name(project_name))
    add(_safe_name(path_prefix))
    add(project_name.replace("(", "").replace(")", ""))
    add(path_prefix.replace("(", "").replace(")", ""))
    return names


def locate_java(java_root: Path, project_name: str, path_prefix: str) -> Optional[Path]:
    for name in java_dir_candidates(project_name, path_prefix):
        path = java_root / name
        if path.is_dir():
            return path
    return None


def extract_one(java_dir: Path, out_dir: Path, project_name: str) -> None:
    ensure_sgr_on_path()
    from symbolic_extractors.multifile.pipeline import process_project
    from symbolic_extractors.multifile.subgraph_dataset import (
        _collect_java_sources,
        catalog_subgraphs,
        load_labels_csv,
    )

    java_files, source_mode = _collect_java_sources(java_dir)
    class_labels = load_labels_csv(java_dir)
    subgraph_catalog = catalog_subgraphs(java_dir)
    process_project(
        source_root=java_dir,
        output_dir=out_dir,
        project_name=project_name,
        java_files=java_files,
        labels={
            "pattern": None,
            "violation": None,
            "commit": None,
            "roles": {},
            "class_labels": class_labels,
        },
        extra_metadata={
            "dpd": {
                "source_project": java_dir.name,
                "source": str(java_dir),
                "source_mode": source_mode,
                "n_java_files": len(java_files),
                "n_class_labels": len(class_labels),
                "n_subgraphs": len(subgraph_catalog),
                "subgraphs": subgraph_catalog,
            }
        },
    )


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


class SummaryLog:
    """One line per project, flushed so a stopped job still leaves the file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh = path.open("w", encoding="utf-8")

    def write(self, project: str, status: str, reason: str = "") -> None:
        if reason:
            line = f"{project}: {status} ({_one_line(reason)})\n"
        else:
            line = f"{project}: {status}\n"
        self._fh.write(line)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Extract SGR for active gold projects only")
    p.add_argument("--java-root", type=Path, default=JAVA_PROJECTS)
    p.add_argument("--out", type=Path, default=SGR_ROOT)
    p.add_argument("--gold", type=Path, default=GOLD_CSV)
    p.add_argument("--summary", type=Path, default=SUMMARY_LOG)
    args = p.parse_args(argv)
    summary = SummaryLog(args.summary)
    print(f"summary {summary.path}")
    try:
        if args.java_root is None or not args.java_root.is_dir():
            reason = f"JAVA_PROJECTS is not a directory: {args.java_root}"
            print(reason, file=sys.stderr)
            summary.write("extract", "Fail", reason)
            return 1
        if not args.gold.is_file():
            reason = f"gold CSV missing: {args.gold}"
            print(reason, file=sys.stderr)
            summary.write("extract", "Fail", reason)
            return 1
        projects = load_active_projects(args.gold)
        print(f"active projects={len(projects)} out={args.out} java={args.java_root}")
        extracted = missing = failed = skipped = 0
        for name, prefix in projects:
            out_dir = args.out / folder_name(name)
            if (out_dir / PROJECT_SGR_FILE).is_file() and (out_dir / "metadata.yaml").is_file():
                print(f"skip existing: {name} -> {out_dir}")
                summary.write(name, "success", "already present")
                skipped += 1
                continue
            java_dir = locate_java(args.java_root, name, prefix)
            if java_dir is None:
                print(f"skip no java tree: {name}", file=sys.stderr)
                summary.write(name, "Fail", "no java tree")
                missing += 1
                continue
            print(f"extract {name}")
            print(f"  java {java_dir}")
            print(f"  out {out_dir}")
            try:
                extract_one(java_dir, out_dir, name)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                print(f"FAIL {name}: {reason}", file=sys.stderr)
                summary.write(name, "Fail", reason)
                failed += 1
                continue
            summary.write(name, "success")
            extracted += 1
        print(
            f"done extracted={extracted} existing={skipped} "
            f"missing={missing} failed={failed}"
        )
        return 1 if missing or failed else 0
    finally:
        summary.close()


if __name__ == "__main__":
    raise SystemExit(main())
