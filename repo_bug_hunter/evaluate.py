"""Grade a run with the official SWE-bench harness: each patch is checked by the project's own tests.

    repo-bug-hunter evaluate runs/baseline
    repo-bug-hunter evaluate runs/baseline --modal      # grade in the cloud on Modal instead

Writes runs/<name>/eval.json with one entry per instance.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .tasks import DATASET

LOGS = Path("logs/run_evaluation")


def collect(run_dir: Path) -> dict:
    """Read the harness's per-instance reports into one summary per instance."""
    name = run_dir.name
    results = {}
    for line in (run_dir / "preds.jsonl").read_text().splitlines():
        pred = json.loads(line)
        iid = pred["instance_id"]
        log_dir = LOGS / name / name / iid
        report = log_dir / "report.json"
        # The Modal runner leaves an empty report.json when an instance errors.
        report_text = report.read_text().strip() if report.exists() else ""
        if not pred["model_patch"].strip():
            results[iid] = {"resolved": False, "error": "empty_patch"}
        elif report_text:
            r = json.loads(report_text)[iid]
            status = r.get("tests_status", {})
            f2p, p2p = status.get("FAIL_TO_PASS", {}), status.get("PASS_TO_PASS", {})
            results[iid] = {
                "resolved": r["resolved"],
                "error": None if r["patch_successfully_applied"] else
                         f"no_test_output: {r.get('infra_failure_reason', 'unknown')}",
                "f2p_passed": f2p.get("success", []), "f2p_failed": f2p.get("failure", []),
                "p2p_passed": len(p2p.get("success", [])), "p2p_failed": p2p.get("failure", []),
            }
        else:
            log = log_dir / "run_instance.log"
            text = log.read_text(errors="replace") if log.exists() else ""
            if ">>>>> Patch Apply Failed" in text:
                error = "patch_apply_failed"
            elif "timed out" in text.lower():
                error = "test_timeout"
            else:
                error = "eval_error: see " + str(log)
            results[iid] = {"resolved": False, "error": error}
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--modal", action="store_true", help="run the harness on Modal (needs a Modal account)")
    ap.add_argument("--collect-only", action="store_true", help="only re-read existing harness logs")
    args = ap.parse_args()

    name = args.run_dir.name
    preds = [json.loads(line) for line in (args.run_dir / "preds.jsonl").read_text().splitlines()]
    if not args.collect_only and any(p["model_patch"].strip() for p in preds):
        cmd = [sys.executable, "-m", "swebench.harness.run_evaluation", "--dataset_name", DATASET,
               "--predictions_path", str(args.run_dir / "preds.jsonl"), "--run_id", name,
               "--max_workers", str(args.workers), "--report_dir", str(args.run_dir)]
        if args.modal:
            cmd += ["--modal", "true"]
        subprocess.run(cmd, check=True)

    results = collect(args.run_dir)
    (args.run_dir / "eval.json").write_text(json.dumps(results, indent=1))
    resolved = sum(r["resolved"] for r in results.values())
    print(f"{name}: resolved {resolved}/{len(results)} -> {args.run_dir / 'eval.json'}")
    print(f"next: repo-bug-hunter analyze {args.run_dir}")


if __name__ == "__main__":
    main()
