"""Run SGR extract then member-graph convert on the same project tree.

Each project directory holds the SGR and the graph written from it::

    <sgr-root>/<Project>/
      metadata.yaml
      sgr_project.yaml
      graph.json
      features/<instance_id>/*.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from keepset import NEIGHBOURHOOD_V2, NEIGHBOURHOODS
from paths import GOLD_CSV, MEMBER_OUT, SGR_ROOT, ensure_sgr_on_path


def _run(mod_main, argv: List[str]) -> int:
    rc = mod_main(argv)
    return int(rc or 0)


def extract_sgr(java_project: Path, sgr_root: Path) -> Path:
    ensure_sgr_on_path()
    from symbolic_extractors.multifile.subgraph_dataset import process_dpd_project

    written = process_dpd_project(java_project, sgr_root)
    out = Path(written["model_graph"]).parent.parent
    print(f"sgr {out}")
    print(f"  model_graph {written['model_graph']}")
    print(f"  metadata {written['metadata']}")
    return out


def cmd_extract(args: argparse.Namespace) -> int:
    extract_sgr(args.project.resolve(), args.sgr_root.resolve())
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    import convert
    import features

    argv = [
        "--sgr-root",
        str(args.sgr_root),
        "--gold",
        str(args.gold),
        "--out",
        str(args.out),
    ]
    if args.project_folder:
        argv.extend(["--project", args.project_folder])
    if args.include_inactive:
        argv.append("--include-inactive")
    argv.extend(["--hops", str(args.hops), "--neighbourhood", args.neighbourhood])
    if args.java_text:
        argv.append("--java-text")
    rc = _run(convert.main, argv)
    if rc:
        return rc
    feat_argv = ["--dataset", str(args.out), "--sgr-root", str(args.sgr_root)]
    if args.project_folder:
        feat_argv.extend(["--project", args.project_folder])
    if args.include_inactive:
        feat_argv.append("--include-inactive")
    return _run(features.main, feat_argv)


def cmd_stamp(args: argparse.Namespace) -> int:
    import inactivate_adapter_wrong_root
    import remap_adapter_as_facade
    import stamp_gold

    argv = [
        "--dataset",
        str(args.out),
        "--sgr-root",
        str(args.sgr_root),
        "--gold",
        str(args.gold),
    ]
    if args.project_folder:
        argv.extend(["--project", args.project_folder])
    if args.dry_run:
        argv.append("--dry-run")
    rc = _run(stamp_gold.main, argv)
    if rc or args.dry_run:
        return rc
    rc = _run(
        remap_adapter_as_facade.main,
        ["--apply", "--dataset", str(args.out)],
    )
    if rc:
        return rc
    return _run(
        inactivate_adapter_wrong_root.main,
        ["--dataset", str(args.out)],
    )


def cmd_all(args: argparse.Namespace) -> int:
    if args.project:
        extract_sgr(args.project.resolve(), args.sgr_root.resolve())
        if not args.project_folder:
            args.project_folder = args.project.resolve().name.replace(" ", "_")
    rc = cmd_convert(args)
    if rc:
        return rc
    if args.skip_stamp:
        return 0
    return cmd_stamp(args)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="SGR extract → member-graph convert (DEPICT)"
    )
    p.add_argument("--sgr-root", type=Path, default=SGR_ROOT)
    p.add_argument("--out", type=Path, default=MEMBER_OUT)
    p.add_argument("--gold", type=Path, default=GOLD_CSV)
    p.add_argument(
        "--project-folder",
        default=None,
        help="SGR folder name under --sgr-root (e.g. JUnit_v3.7)",
    )
    p.add_argument("--include-inactive", action="store_true")
    p.add_argument(
        "--hops",
        type=int,
        default=1,
        help="Type-hop radius from each gold type (members share their type's hop)",
    )
    p.add_argument(
        "--neighbourhood",
        choices=NEIGHBOURHOODS,
        default=NEIGHBOURHOOD_V2,
        help="v2 is the paper's collaboration neighbourhood (default). v1 is the original keep-set",
    )
    p.add_argument(
        "--java-text",
        action="store_true",
        help="Read snippet text from JAVA_PROJECTS when the SGR has no sources",
    )
    p.add_argument("--dry-run", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="Java tree -> sgr_project.yaml")
    pe.add_argument("--project", type=Path, required=True)
    pe.set_defaults(func=cmd_extract)

    pc = sub.add_parser("convert", help="SGR -> graph.json + feature CSVs")
    pc.set_defaults(func=cmd_convert)

    ps = sub.add_parser("stamp", help="spine stamp, Adapter remap, wrong-root off")
    ps.set_defaults(func=cmd_stamp)

    pa = sub.add_parser("all", help="extract (optional) + convert + stamp")
    pa.add_argument("--project", type=Path, default=None, help="Java project to extract first")
    pa.add_argument("--skip-stamp", action="store_true")
    pa.set_defaults(func=cmd_all)

    args = p.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
