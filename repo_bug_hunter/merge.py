"""Combine results graded in separate places into one run: trajectories, grades and settings.
The GitHub Actions workflow runs each task in its own job and merges them with this.

    repo-bug-hunter merge downloaded/result-* --into runs/baseline

Each source is a directory that holds runs/<name>/ (config.json, trajs/, eval.json), such as
a job's uploaded artifact. A task found in several places keeps the version merged last.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .run import SAME, write_preds


def merge(sources: list[Path], run_dir: Path) -> int:
    """Merges into run_dir and returns how many trajectories were added or replaced."""
    name = run_dir.name
    cfg_path, eval_path = run_dir / "config.json", run_dir / "eval.json"
    config = json.loads(cfg_path.read_text()) if cfg_path.exists() else None
    evals = json.loads(eval_path.read_text()) if eval_path.exists() else {}
    (run_dir / "trajs").mkdir(parents=True, exist_ok=True)
    merged = 0
    for src in sources:
        part = src / "runs" / name
        if not (part / "config.json").exists():
            continue
        part_config = json.loads((part / "config.json").read_text())
        if config is None:
            config = part_config
        for key in SAME:
            if config.get(key) != part_config.get(key):
                raise ValueError(f"{src} was run with {key}={part_config.get(key)!r}, "
                                 f"but {run_dir} with {config.get(key)!r}")
        config["instances"] = sorted(set(config["instances"]) | set(part_config["instances"]))
        for traj in sorted((part / "trajs").glob("*.json")):
            shutil.copy(traj, run_dir / "trajs" / traj.name)
            evals.pop(traj.stem, None)  # an old grade belongs to the old patch
            merged += 1
        if (part / "eval.json").exists():
            evals.update(json.loads((part / "eval.json").read_text()))
    if config is None:
        raise ValueError(f"no results for {name} in {', '.join(map(str, sources)) or 'no sources'}")
    cfg_path.write_text(json.dumps(config, indent=2))
    graded = {p.stem for p in (run_dir / "trajs").glob("*.json")}
    eval_path.write_text(json.dumps({k: v for k, v in evals.items() if k in graded}, indent=1))
    write_preds(run_dir)
    return merged


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*", type=Path)
    ap.add_argument("--into", type=Path, required=True, help="the run to merge into, e.g. runs/baseline")
    args = ap.parse_args()
    try:
        n = merge(args.sources, args.into)
    except ValueError as e:
        ap.error(str(e))
    print(f"merged {n} trajectories into {args.into}")


if __name__ == "__main__":
    main()
