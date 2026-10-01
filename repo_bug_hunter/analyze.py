"""Turn trajectories and grades into numbers: resolve rate, cost, steps, and why tasks failed.

    repo-bug-hunter analyze runs/baseline                         # one run
    repo-bug-hunter analyze runs/baseline runs/test_first         # before/after on the shared tasks
    repo-bug-hunter analyze runs/baseline runs/test_first --out results.md
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter
from pathlib import Path

from .run import RETRY, hit_daily_limit
from .tasks import patch_files

MIN_CI_TASKS = 20  # below this, a bootstrap interval is misleading (with 1 task it is always one point)

CATEGORIES = {
    "gave_up": "Gave up: hit the step/cost limit or refused without submitting, or submitted an empty patch",
    "wrong_file": "Wrong file: the patch touches none of the files the real fix changed",
    "broke_other_tests": "Broke other tests: previously passing tests now fail",
    "tests_still_fail": "Right file, but the issue's tests still fail",
    "patch_did_not_apply": "Patch did not apply cleanly",
    "eval_error": "Grader produced no test results (timeout or crash)",
    "run_error": "Agent run crashed (Docker or API error)",
}


def classify(traj: dict, ev: dict) -> str | None:
    """Why an unresolved task failed; the first matching reason wins. None if resolved."""
    if ev.get("resolved"):
        return None
    if traj["exit_status"] in ("env_error", "api_error"):
        return "run_error"
    if traj["exit_status"] != "submitted" or not traj["patch"].strip():
        return "gave_up"
    error = ev.get("error") or ""
    if error == "patch_apply_failed":
        return "patch_did_not_apply"
    if error:
        return "eval_error"
    if not set(patch_files(traj["patch"])) & set(traj["task"]["gold_files"]):
        return "wrong_file"
    if ev.get("p2p_failed"):
        return "broke_other_tests"
    return "tests_still_fail"


def load_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    eval_path = run_dir / "eval.json"
    if not eval_path.exists():
        raise SystemExit(f"{eval_path} not found; run: repo-bug-hunter evaluate {run_dir}")
    evals = json.loads(eval_path.read_text())
    rows, unfinished = [], []
    for path in sorted((run_dir / "trajs").glob("*.json")):
        t = json.loads(path.read_text())
        if t["exit_status"] in RETRY:
            # Stopped by Docker, the API or a daily limit: not the agent's result, and redone on
            # resume. Counted apart, so partial work is never scored as a success or a failure.
            unfinished.append({"id": t["instance_id"], "exit_status": t["exit_status"],
                               "daily_limit": hit_daily_limit(t)})
            continue
        ev = evals.get(t["instance_id"])
        if ev is None:
            continue  # not graded yet
        rows.append({
            "id": t["instance_id"], "repo": t["task"]["repo"], "difficulty": t["task"]["difficulty"],
            "resolved": bool(ev["resolved"]), "category": classify(t, ev),
            "cost": round(t["cost"], 4), "steps": t["n_steps"], "tool_calls": t["n_tool_calls"],
            "tokens": sum(sum(s["usage"].values()) for s in t["steps"]),
            "seconds": t["seconds"], "exit_status": t["exit_status"],
            "patch_files": patch_files(t["patch"]), "gold_files": t["task"]["gold_files"],
            "eval_error": ev.get("error"), "fallback_used": t.get("fallback_used", False),
            "repro": t.get("repro"),
        })
    config = json.loads((run_dir / "config.json").read_text())
    return {"name": run_dir.name, "config": config, "rows": rows, "unfinished": unfinished}


# ---- statistics -------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for a proportion; behaves well at small n."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denom = 1 + z * z / n
    return (centre - margin) / denom, (centre + margin) / denom


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided p-value that two paired variants have the same resolve rate.
    Only tasks where exactly one variant succeeded carry information."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def bootstrap_delta(a: list[int], b: list[int], iters: int = 10_000, seed: int = 0) -> tuple[float, float]:
    """95% CI for mean(b) - mean(a), resampling tasks with replacement (paired)."""
    diffs = [y - x for x, y in zip(a, b)]
    n, rng = len(diffs), random.Random(seed)
    samples = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return samples[int(0.025 * iters)], samples[int(0.975 * iters) - 1]


# ---- summaries ----------------------------------------------------------------

def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    k = sum(r["resolved"] for r in rows)
    costs, steps = [r["cost"] for r in rows], [r["steps"] for r in rows]
    s = {
        "n": n, "resolved": k, "rate": k / n if n else 0.0, "ci": wilson(k, n),
        "cost_total": sum(costs), "cost_mean": statistics.fmean(costs) if n else 0.0,
        "cost_median": statistics.median(costs) if n else 0.0,
        "cost_per_resolved": sum(costs) / k if k else None,
        "steps_mean": statistics.fmean(steps) if n else 0.0,
        "steps_median": statistics.median(steps) if n else 0.0,
        "tokens_mean": statistics.fmean(r["tokens"] for r in rows) if n else 0.0,
        "exit_status": dict(Counter(r["exit_status"] for r in rows)),
        "categories": {c: sum(r["category"] == c for r in rows) for c in CATEGORIES},
        "by_difficulty": _breakdown(rows, "difficulty"),
        "by_repo": _breakdown(rows, "repo"),
        "fallback_runs": sum(r["fallback_used"] for r in rows),
    }
    if any(r["repro"] for r in rows):
        s["repro"] = {
            "by_status": _breakdown([r for r in rows if r["repro"]], lambda r: r["repro"]["status"]),
            "by_passed_at_submit": _breakdown(
                [r for r in rows if r["repro"] and r["repro"]["status"] == "reproduced"],
                lambda r: {True: "passed", False: "still failing", None: "not submitted"}[r["repro"]["passed_at_submit"]]),
        }
    return s


def _breakdown(rows: list[dict], key) -> dict:
    get = key if callable(key) else (lambda r: r[key])
    out: dict = {}
    for r in rows:
        group = out.setdefault(str(get(r)), [0, 0])
        group[0] += r["resolved"]
        group[1] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1][1]))


def compare(a: dict, b: dict) -> dict:
    """Paired comparison on the tasks both runs attempted."""
    rows_a = {r["id"]: r for r in a["rows"]}
    rows_b = {r["id"]: r for r in b["rows"]}
    ids = sorted(rows_a.keys() & rows_b.keys())
    ra = [int(rows_a[i]["resolved"]) for i in ids]
    rb = [int(rows_b[i]["resolved"]) for i in ids]
    only_a = [i for i, x, y in zip(ids, ra, rb) if x and not y]
    only_b = [i for i, x, y in zip(ids, ra, rb) if y and not x]
    sa = summarize([rows_a[i] for i in ids])
    sb = summarize([rows_b[i] for i in ids])
    return {
        "a": a["name"], "b": b["name"], "n": len(ids), "summary_a": sa, "summary_b": sb,
        "delta": sb["rate"] - sa["rate"],
        "delta_ci": bootstrap_delta(ra, rb) if len(ids) >= MIN_CI_TASKS else None,
        "p_value": mcnemar_exact(len(only_a), len(only_b)),
        "both": sum(x and y for x, y in zip(ra, rb)), "neither": sum(not x and not y for x, y in zip(ra, rb)),
        "only_a": only_a, "only_b": only_b,
    }


# ---- markdown -------------------------------------------------------------------

def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _money(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.2f}"


def summary_md(run: dict) -> str:
    s, c = summarize(run["rows"]), run["config"]
    lines = [
        f"## {run['name']}: {c['variant']}, {c['model']}" + (f", effort {c['effort']}" if c.get("effort") else ""),
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Resolved | {s['resolved']}/{s['n']} ({_pct(s['rate'])}, 95% CI {_pct(s['ci'][0])}–{_pct(s['ci'][1])}) |",
        f"| Cost per issue | {_money(s['cost_mean'])} mean, {_money(s['cost_median'])} median ({_money(s['cost_total'])} total) |",
        f"| Cost per resolved issue | {_money(s['cost_per_resolved'])} |",
        f"| Steps per issue | {s['steps_mean']:.1f} mean, {s['steps_median']:.0f} median |",
        f"| Tokens per issue | {s['tokens_mean'] / 1000:,.0f}k mean |",
        f"| Exit status | {', '.join(f'{k} {v}' for k, v in sorted(s['exit_status'].items()))} |",
    ]
    if run.get("unfinished"):
        u = run["unfinished"]
        daily = sum(x["daily_limit"] for x in u)
        why = ("stopped at the daily request limit" if daily == len(u) else
               "stopped by Docker or API errors" if not daily else "stopped by errors or the daily request limit")
        lines.append(f"| Not finished | {len(u)} ({why}): not counted above. Run the same command "
                     f"(or workflow) again to finish them: {', '.join(x['id'] for x in u)} |")
    if s["fallback_runs"]:
        lines.append(f"| Runs with a refusal fallback | {s['fallback_runs']} (part of the run used another model) |")
    unresolved = s["n"] - s["resolved"]
    lines += ["", f"### Why the {unresolved} unresolved task{'' if unresolved == 1 else 's'} failed", "",
              "| Category | Count |", "|---|---|"]
    lines += [f"| {CATEGORIES[k]} | {v} |" for k, v in s["categories"].items() if v]
    lines += ["", "### By difficulty", "", "| Difficulty | Resolved |", "|---|---|"]
    lines += [f"| {d} | {k}/{n} |" for d, (k, n) in s["by_difficulty"].items()]
    if "repro" in s:
        lines += ["", "### Test-first: did the reproduction help?", "",
                  "| Reproduction | Resolved |", "|---|---|"]
        lines += [f"| {st} | {k}/{n} |" for st, (k, n) in s["repro"]["by_status"].items()]
        lines += [f"| reproduced, test {st} at submit | {k}/{n} |"
                  for st, (k, n) in s["repro"]["by_passed_at_submit"].items()]
    return "\n".join(lines)


def compare_md(c: dict) -> str:
    sa, sb, a, b = c["summary_a"], c["summary_b"], c["a"], c["b"]
    interval = (f"95% CI {100 * c['delta_ci'][0]:+.0f} to {100 * c['delta_ci'][1]:+.0f}" if c["delta_ci"]
                else f"too few tasks for an interval; use {MIN_CI_TASKS}+")
    verdict = ("The difference is unlikely to be noise (p < 0.05)." if c["p_value"] < 0.05 else
               "At this sample size the difference could be noise (p ≥ 0.05): run more tasks, "
               "or repeat both runs, before claiming an improvement.")
    lines = [
        f"## {a} vs {b} ({c['n']} shared task{'' if c['n'] == 1 else 's'})",
        "",
        f"| | {a} | {b} | Change |",
        "|---|---|---|---|",
        f"| Resolved | {sa['resolved']} ({_pct(sa['rate'])}) | {sb['resolved']} ({_pct(sb['rate'])}) | "
        f"{100 * c['delta']:+.1f} pts ({interval}) |",
        f"| Mean cost per issue | {_money(sa['cost_mean'])} | {_money(sb['cost_mean'])} | "
        f"{sb['cost_mean'] - sa['cost_mean']:+.2f} |",
        f"| Cost per resolved issue | {_money(sa['cost_per_resolved'])} | {_money(sb['cost_per_resolved'])} | |",
        f"| Mean steps per issue | {sa['steps_mean']:.1f} | {sb['steps_mean']:.1f} | "
        f"{sb['steps_mean'] - sa['steps_mean']:+.1f} |",
        f"| Mean tokens per issue | {sa['tokens_mean'] / 1000:,.0f}k | {sb['tokens_mean'] / 1000:,.0f}k | |",
        "",
        f"Paired: both solved {c['both']}, only {a} {len(c['only_a'])}, only {b} {len(c['only_b'])}, "
        f"neither {c['neither']}. McNemar exact p = {c['p_value']:.3f}. {verdict}",
        "",
        "### Failure categories",
        "",
        f"| Category | {a} | {b} |",
        "|---|---|---|",
    ]
    lines += [f"| {CATEGORIES[k]} | {sa['categories'][k]} | {sb['categories'][k]} |"
              for k in CATEGORIES if sa["categories"][k] or sb["categories"][k]]
    lines += ["", "### Tasks that flipped", "",
              f"- Only {b} solved: {', '.join(c['only_b']) or 'none'}",
              f"- Only {a} solved: {', '.join(c['only_a']) or 'none'}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path, help="one run, or two runs to compare (before, after)")
    ap.add_argument("--out", type=Path, help="also write the report to this markdown file")
    args = ap.parse_args()
    if len(args.runs) > 2:
        ap.error("give one or two runs")

    runs = [load_run(r) for r in args.runs]
    parts = [summary_md(r) for r in runs]
    if len(runs) == 2:
        parts.insert(0, compare_md(compare(*runs)))
    report = "\n\n".join(parts) + "\n"
    print(report)
    if args.out:
        args.out.write_text(report)


if __name__ == "__main__":
    main()
