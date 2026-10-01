"""Run the agent on a subset of SWE-bench Verified.

    repo-bug-hunter run --name baseline --n 50                              # a free OpenRouter model (the default)
    repo-bug-hunter run --name test_first --variant test_first --n 50
    repo-bug-hunter run --name opus --model claude-opus-5-5                 # Claude; key in ANTHROPIC_API_KEY
    repo-bug-hunter run --name local --provider ollama --model gemma4-32k --workers 1

Writes runs/<name>/trajs/<instance_id>.json and runs/<name>/preds.jsonl.
Re-running the same command resumes: finished instances are skipped. If the provider's daily
request limit runs out, the run stops early; run the same command after the limit resets.
"""

from __future__ import annotations

import argparse
import json
import threading
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .agent import run_agent, system_prompt
from .env import DockerEnv, docker_available
from .providers import add_model_args, make_query
from .providers import check as check_model
from .providers import resolve as resolve_model
from .tasks import DATASET, load_tasks, patch_files, select
from .tools import tool_schemas

RUNS = Path("runs")
RETRY = ("env_error", "api_error")  # runs that failed for infrastructure reasons are retried on resume
# Settings that must not change within a run: they change what the agent can do on a task.
SAME = ("variant", "provider", "model", "effort", "max_steps", "max_cost")


def changed_setting(old: dict, new: dict) -> str | None:
    """The first setting in SAME that differs between a run's recorded config and a new one."""
    return next((key for key in SAME if key in old and old.get(key) != new.get(key)), None)


def hit_daily_limit(traj: dict) -> bool:
    """Whether the run stopped because the provider's daily request limit is used up."""
    return (traj.get("error") or "").startswith("DailyLimitReached")


def error_traj(task: dict, variant: str, model: str) -> dict:
    """The trajectory of a task whose run crashed (Docker, or anything else outside the agent)."""
    return {"instance_id": task["instance_id"], "variant": variant, "model": model,
            "problem_statement": task["problem_statement"], "steps": [], "patch": "",
            "exit_status": "env_error", "error": traceback.format_exc(limit=5),
            "cost": 0.0, "n_steps": 0, "n_tool_calls": 0, "seconds": 0}


def needs_run(path: Path, redo: bool = False) -> bool:
    return redo or not path.exists() or json.loads(path.read_text())["exit_status"] in RETRY


def write_preds(run_dir: Path) -> list[dict]:
    """preds.jsonl, the grader's input: one patch per finished trajectory. Runs stopped by Docker,
    the API or a daily limit are left out: they are redone on resume, so grading their partial
    work would only mislead. Returns every trajectory."""
    trajs = [json.loads(p.read_text()) for p in sorted((run_dir / "trajs").glob("*.json"))]
    with open(run_dir / "preds.jsonl", "w") as f:
        for t in trajs:
            if t["exit_status"] in RETRY:
                continue
            f.write(json.dumps({"instance_id": t["instance_id"], "model_name_or_path": run_dir.name,
                                "model_patch": t["patch"]}) + "\n")
    return trajs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="results go to runs/<name>/")
    ap.add_argument("--variant", choices=["baseline", "test_first"], default="baseline")
    add_model_args(ap)
    ap.add_argument("--difficulty", help='only tasks with this SWE-bench difficulty label, e.g. "<15 min fix"')
    ap.add_argument("--n", type=int, default=50, help="number of tasks to sample")
    ap.add_argument("--seed", type=int, default=0, help="sampling seed; keep it fixed across variants")
    ap.add_argument("--instances", nargs="+", help="explicit instance ids instead of --n/--seed")
    ap.add_argument("--workers", type=int, default=2, help="tasks run in parallel")
    ap.add_argument("--max-steps", type=int, default=50)
    ap.add_argument("--max-cost", type=float, default=2.0,
                    help="USD per task; applies to Claude and to paid OpenRouter models")
    ap.add_argument("--redo", action="store_true", help="re-run tasks that already have a trajectory")
    ap.add_argument("--print-instances", action="store_true",
                    help="print the selected tasks that still need a run, as a JSON list, and exit")
    args = ap.parse_args()
    try:
        model = resolve_model(args, need_key=not args.print_instances)
    except ValueError as e:
        ap.error(str(e))
    if not args.print_instances:
        # Fail now, not once per task, if something the run needs is down.
        if not docker_available():
            ap.error("Docker is not running; start Docker Desktop (or Colima, OrbStack) and try again")
        if problem := check_model(model):
            ap.error(problem)

    tasks = load_tasks()
    if args.difficulty:
        tasks = [t for t in tasks if t.get("difficulty") == args.difficulty]
    if args.instances:
        wanted = set(args.instances)
        tasks = [t for t in tasks if t["instance_id"] in wanted]
        if missing := wanted - {t["instance_id"] for t in tasks}:
            ap.error(f"no such task{'s' if len(missing) > 1 else ''}"
                     f"{' with this --difficulty' if args.difficulty else ''}: {', '.join(sorted(missing))}")
    else:
        tasks = select(tasks, args.n, args.seed)

    out = RUNS / args.name
    config = {"dataset": DATASET, "variant": args.variant, **model.config(),
              "difficulty": args.difficulty, "max_steps": args.max_steps, "max_cost": args.max_cost,
              "seed": args.seed, "instances": [t["instance_id"] for t in tasks]}
    cfg_path = out / "config.json"
    if cfg_path.exists():
        old = json.loads(cfg_path.read_text())
        if key := changed_setting(old, config):
            ap.error(f"{out} was run with {key}={old[key]!r}; use another --name")
        config["instances"] = sorted(set(old["instances"]) | set(config["instances"]))
    if args.print_instances:
        print(json.dumps([t["instance_id"] for t in tasks
                          if needs_run(out / "trajs" / f"{t['instance_id']}.json", args.redo)]))
        return
    (out / "trajs").mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, indent=2))

    test_first = args.variant == "test_first"
    query = make_query(model, system_prompt(test_first), tool_schemas(test_first))
    out_of_requests = threading.Event()

    def solve(task: dict) -> None:
        iid = task["instance_id"]
        path = out / "trajs" / f"{iid}.json"
        if out_of_requests.is_set() or not needs_run(path, args.redo):
            return
        print(f"[{iid}] starting", flush=True)
        try:
            with DockerEnv(task["image"]) as env:
                traj = run_agent(task, env, query, model=model.name, test_first=test_first,
                                 max_steps=args.max_steps, max_cost=args.max_cost,
                                 log=lambda s: print(f"[{iid}] {s}", flush=True))
        except Exception:
            traj = error_traj(task, args.variant, model.name)
            print(f"[{iid}] {traj['error'].strip().splitlines()[-1][:300]}", flush=True)
        traj["task"] = {"repo": task["repo"], "difficulty": task.get("difficulty"),
                        "gold_files": patch_files(task["patch"])}
        path.write_text(json.dumps(traj, indent=1, default=str))
        print(f"[{iid}] {traj['exit_status']} after {traj['n_steps']} steps, ${traj['cost']:.2f}", flush=True)
        if hit_daily_limit(traj):
            out_of_requests.set()  # the tasks still queued would only fail the same way
            print(f"[{iid}] {traj['error']}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(solve, tasks))

    trajs = write_preds(out)
    print(f"\n{len(trajs)} trajectories in {out}/trajs, total ${sum(t['cost'] for t in trajs):.2f}")
    print("exit status:", dict(Counter(t["exit_status"] for t in trajs)))
    if out_of_requests.is_set():
        print("Stopped early: the daily request limit is used up. Run the same command after it resets; "
              "finished tasks are kept.")
    print(f"next: repo-bug-hunter evaluate {out}")


if __name__ == "__main__":
    main()
