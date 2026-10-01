"""Build the static replay site: one page, plus JSON data. Host it anywhere (e.g. GitHub Pages).

    repo-bug-hunter viewer runs/baseline runs/test_first --out site
    python -m http.server -d site 8000        # preview at http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .analyze import CATEGORIES, compare, load_run, summarize

PAGE = Path(__file__).resolve().parent / "site" / "index.html"  # shipped inside the package


def build(run_dirs: list[Path], out: Path) -> None:
    runs = [load_run(d) for d in run_dirs]
    manifest = {"categories": CATEGORIES, "runs": [], "comparison": None}
    for run_dir, run in zip(run_dirs, runs):
        evals = json.loads((run_dir / "eval.json").read_text())
        data = out / "data" / run["name"]
        data.mkdir(parents=True, exist_ok=True)
        for row in run["rows"]:
            traj = json.loads((run_dir / "trajs" / f"{row['id']}.json").read_text())
            traj["eval"], traj["category"] = evals[row["id"]], row["category"]
            (data / f"{row['id']}.json").write_text(json.dumps(traj))
        config = {k: v for k, v in run["config"].items() if k != "instances"}
        manifest["runs"].append({"name": run["name"], "config": config,
                                 "summary": summarize(run["rows"]), "rows": run["rows"]})
    if len(runs) == 2:
        c = compare(*runs)
        manifest["comparison"] = {k: v for k, v in c.items() if not k.startswith("summary_")}
        manifest["comparison"].update(rate_a=c["summary_a"]["rate"], rate_b=c["summary_b"]["rate"])
    (out / "data" / "manifest.json").write_text(json.dumps(manifest))
    shutil.copy(PAGE, out / "index.html")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("site"))
    args = ap.parse_args()
    build(args.runs, args.out)
    print(f"Built {args.out}/. Preview: python -m http.server -d {args.out} 8000")


if __name__ == "__main__":
    main()
