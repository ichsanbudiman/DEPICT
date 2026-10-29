"""Copy the Graph-DPD files convert reads.

The dataset root and the SSH host come from the repo-root ``.env`` file.
That file stays on the machine and is not committed.

  python3 graph_dataset_extractor/fetch_graph_dpd.py
  python3 graph_dataset_extractor/fetch_graph_dpd.py \\
    --project coala --project cruisecontrol --project "Lexi v0.1.1 alpha"
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from envfile import load_depict_env
from paths import GOLD_CSV, SGR_ROOT

NEEDED = ("metadata.yaml", "model_graph/project.yaml")


def folder_name(project_name: str) -> str:
    return project_name.replace(" ", "_")


def active_projects(gold_csv: Path) -> List[Tuple[str, str]]:
    seen = []
    have = set()
    with gold_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("active", "")).strip() not in {"1", "true", "True"}:
                continue
            pn = row["project_name"]
            dest = folder_name(pn)
            if dest in have:
                continue
            have.add(dest)
            seen.append((pn, dest))
    return seen


def named_projects(names: List[str]) -> List[Tuple[str, str]]:
    seen = []
    have = set()
    for raw in names:
        pn = raw.strip()
        if not pn:
            continue
        dest = folder_name(pn)
        if dest in have:
            continue
        have.add(dest)
        seen.append((pn, dest))
    return seen


def remote_candidates(project_name: str, dest: str) -> List[str]:
    names = [dest, project_name]
    extra = dest.replace("_(", "_").replace(")", "")
    if extra not in names:
        names.append(extra)
    out = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def copy_local(src_root: Path, remote_dir: str, dest_dir: Path) -> bool:
    src = src_root / remote_dir
    meta = src / "metadata.yaml"
    yaml = src / "model_graph" / "project.yaml"
    if not meta.is_file() or not yaml.is_file():
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "model_graph").mkdir(parents=True, exist_ok=True)
    shutil.copy2(meta, dest_dir / "metadata.yaml")
    shutil.copy2(yaml, dest_dir / "model_graph" / "project.yaml")
    return True


def rsync_ssh(ssh: str, src_root: Path, remote_dir: str, dest_dir: Path) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "model_graph").mkdir(parents=True, exist_ok=True)
    remote = f"{ssh}:{src_root}/{remote_dir}"
    ok = True
    cmds = [
        ["rsync", "-a", f"{remote}/metadata.yaml", str(dest_dir / "metadata.yaml")],
        [
            "rsync",
            "-a",
            f"{remote}/model_graph/project.yaml",
            str(dest_dir / "model_graph" / "project.yaml"),
        ],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            ok = False
            break
    if not ok:
        for p in (dest_dir / "metadata.yaml", dest_dir / "model_graph" / "project.yaml"):
            if p.is_file() and p.stat().st_size == 0:
                p.unlink()
    return ok and (dest_dir / "metadata.yaml").is_file() and (
        dest_dir / "model_graph" / "project.yaml"
    ).is_file()


def main(argv: Optional[List[str]] = None) -> int:
    env_path = load_depict_env()
    src_default = os.environ.get("GRAPH_DPD_SRC", "").strip()
    if not src_default:
        print(f"GRAPH_DPD_SRC is unset in {env_path}", file=sys.stderr)
        return 1
    ssh_default = os.environ.get("GRAPH_DPD_SSH", "").strip() or None
    p = argparse.ArgumentParser(description="Fetch Graph-DPD files convert needs")
    p.add_argument("--src", type=Path, default=Path(src_default))
    p.add_argument("--out", type=Path, default=SGR_ROOT)
    p.add_argument("--gold", type=Path, default=GOLD_CSV)
    p.add_argument(
        "--ssh",
        default=ssh_default,
        help="user@host when GRAPH_DPD_SRC is not mounted here (default: GRAPH_DPD_SSH)",
    )
    p.add_argument(
        "--project",
        action="append",
        default=[],
        help="Fetch this folder only (repeatable). Does not need to be in the gold CSV.",
    )
    args = p.parse_args(argv)
    projects = named_projects(args.project) if args.project else active_projects(args.gold)
    print(f"env={env_path.name}")
    print(f"projects={len(projects)}")
    print(f"src={args.src}")
    print(f"out={args.out}")
    if args.ssh:
        print(f"ssh={args.ssh}")
    elif not args.src.is_dir():
        print(
            f"GRAPH_DPD_SRC is not on this machine ({args.src}). "
            f"Set GRAPH_DPD_SSH in .env to copy it over SSH.",
            file=sys.stderr,
        )
        return 1

    ok = miss = []
    missed: List[str] = []
    got: List[str] = []
    for pn, dest in projects:
        dest_dir = args.out / dest
        done = False
        used = None
        for cand in remote_candidates(pn, dest):
            if args.ssh:
                done = rsync_ssh(args.ssh, args.src, cand, dest_dir)
            else:
                done = copy_local(args.src, cand, dest_dir)
            if done:
                used = cand
                break
        if done:
            got.append(f"{dest}  (from {used})")
            print(f"  ok  {dest}" + (f"  <- {used}" if used != dest else ""))
        else:
            missed.append(dest)
            print(f"  MISS {dest}  tried {remote_candidates(pn, dest)}")

    print(f"\nfetched={len(got)}  missing={len(missed)}")
    if missed:
        print("missing folders:")
        for m in missed:
            print(f"  {m}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
