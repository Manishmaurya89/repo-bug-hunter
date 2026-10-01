"""Check the model setup end to end on a toy bug, in minutes: free with OpenRouter's free models.

    repo-bug-hunter smoke                                       # the default free OpenRouter model
    repo-bug-hunter smoke --variant test_first
    repo-bug-hunter smoke --model claude-sonnet-5-5             # Claude, a few cents
    repo-bug-hunter smoke --provider ollama --model gemma4-32k
    repo-bug-hunter smoke --local                               # without Docker (see below)

The toy repository lives in a throwaway Docker container (python:3.12, no network), like the real
tasks. --local runs the agent's commands on this machine instead, in a temporary directory, with
API keys withheld from their environment: use it only where Docker isn't available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .agent import run_agent, system_prompt
from .env import DockerEnv, LocalEnv, docker_available
from .providers import add_model_args, make_query
from .providers import check as check_model
from .providers import resolve as resolve_model
from .tools import tool_schemas

IMAGE = "python:3.12"  # has git, which the harness uses to extract the patch
FILES = {
    "stats.py": "def mean(xs):\n    return sum(xs) / len(xs)\n\n\n"
                "def median(xs):\n    xs = sorted(xs)\n    return xs[len(xs) // 2]\n",
    "tests/test_stats.py": "from stats import mean\n\n\ndef test_mean():\n    assert mean([1, 2, 3]) == 2\n",
}
TASK = {"instance_id": "toy__stats-1", "repo": "toy/stats", "problem_statement":
        "median() is wrong for lists with an even number of items: median([1, 2, 3, 4]) returns 3, "
        "but the median is 2.5."}
CHECK = "python3 -c 'from stats import median; assert median([1, 2, 3, 4]) == 2.5; assert median([3, 1, 2]) == 2'"
COMMIT = "git init -q && git add . && git -c user.name=smoke -c user.email=smoke@localhost commit -qm 'toy repo'"


def docker_repo() -> DockerEnv:
    return DockerEnv(IMAGE, platform=None)  # a native image: no emulation needed


def local_repo(tmp: Path) -> LocalEnv:
    root = tmp / "repo"
    root.mkdir()
    # SWE-bench containers have `python`; many Macs only have `python3`. Match the containers.
    shims = tmp / "bin"
    shims.mkdir()
    (shims / "python").symlink_to(sys.executable)
    os.environ["PATH"] = f"{shims}{os.pathsep}{os.environ['PATH']}"
    return LocalEnv(root)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_model_args(ap)
    ap.add_argument("--variant", choices=["baseline", "test_first"], default="baseline")
    ap.add_argument("--local", action="store_true",
                    help="run without Docker: the model's commands run on this machine (not sandboxed)")
    args = ap.parse_args()
    try:
        model = resolve_model(args)
    except ValueError as e:
        ap.error(str(e))
    if not args.local and not docker_available():
        ap.error("Docker is not running. Start it, or pass --local to run the model's commands on "
                 "this machine instead (not sandboxed).")
    if problem := check_model(model):
        ap.error(problem)

    tmp = Path(tempfile.mkdtemp(prefix="repo-bug-hunter-smoke-"))
    test_first = args.variant == "test_first"
    with (local_repo(tmp) if args.local else docker_repo()) as env:
        for name, text in FILES.items():
            env.write(f"{env.workdir}/{name}", text.encode())
        code, out = env.run(COMMIT, timeout=60)
        if code != 0:
            sys.exit(f"could not create the toy repository: {out}")
        query = make_query(model, system_prompt(test_first, env.workdir), tool_schemas(test_first))
        traj = run_agent(TASK, env, query, model=model.name, test_first=test_first,
                         max_steps=20, max_cost=0.5, log=lambda s: print(s, flush=True))
        fixed = env.run(CHECK, timeout=60)[0] == 0

    print(f"\n{traj['exit_status']} in {traj['n_steps']} steps, ${traj['cost']:.3f}; bug fixed: {fixed}")
    if model.native_claude:
        cached = sum(s["usage"]["cache_read_input_tokens"] for s in traj["steps"][1:])
        print(f"cache reads after the first step: {cached} tokens"
              + ("" if cached or traj["n_steps"] < 2 else "  <- prompt caching is not working"))
    if traj.get("error"):
        print("error:", traj["error"])
    if test_first:
        print("reproduction:", traj["repro"])
    print("\n" + (traj["patch"] or "(empty patch)"))
    saved = tmp / "trajectory.json"
    saved.write_text(json.dumps(traj, indent=1, default=str))
    print(f"full trajectory: {saved}")
    sys.exit(0 if fixed else 1)


if __name__ == "__main__":
    main()
