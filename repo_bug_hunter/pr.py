"""Test the agent on real bug fixes from GitHub, outside SWE-bench: fresh bugs no model has seen.

    repo-bug-hunter pr more-itertools/more-itertools#1305 --name fresh
    repo-bug-hunter pr python-humanize/humanize#334 --name fresh --provider ollama --model gemma4-32k
    repo-bug-hunter pr owner/repo#123:120 --name fresh      # PR 123, whose issue (120) isn't linked in its text

Each argument is a merged pull request that fixes an issue. The agent gets the issue's text and
the repository as it was just before the fix, and its patch is graded by the tests the
maintainers added with the fix, the way SWE-bench builds its tasks:

1. A Docker image holds the repository at the commit before the fix, with its test dependencies.
2. The fix is split into its code change and its test change. With the new tests in place, the
   tests are run without and with the code change: tests that fail without it and pass with it
   must pass (FAIL_TO_PASS), and tests that pass both times must keep passing (PASS_TO_PASS).
3. The agent works in a container of that image, without network.
4. Its patch is applied in a fresh container together with the fix's tests, which are run.

Results go to runs/<name>/ in the usual layout, so `repo-bug-hunter analyze` and `repo-bug-hunter viewer` work.
GitHub allows 60 API requests an hour without a token, and each pull request needs two or three;
set GITHUB_TOKEN to raise that. Only Python projects tested with pytest are supported.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import PurePosixPath

from .agent import run_agent, system_prompt
from .env import DockerEnv, docker_available, quote
from .providers import TLS, add_model_args, make_query
from .providers import check as check_model
from .providers import resolve as resolve_model
from .run import RETRY, RUNS, changed_setting, error_traj, hit_daily_limit, needs_run, write_preds
from .tasks import patch_files
from .tools import is_test_path, tool_schemas

API = "https://api.github.com"
PASSING = ("PASSED", "XFAIL")
PYTEST = "python -m pytest -rA -p no:cacheprovider -q --color=no"
# Installs the project and what its tests need: a test extra or dependency group from
# pyproject.toml (or "dev" if there's no test one), requirements files meant for tests, and pytest.
INSTALL = r'''
import os, subprocess, sys
def pip(*args):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *args])
try:
    import tomllib
    meta = tomllib.load(open("pyproject.toml", "rb"))
except (ImportError, OSError):
    meta = {}
def pick(names):
    return next((n for n in ("test", "tests", "testing") if n in names), "dev" if "dev" in names else None)
extra = pick(meta.get("project", {}).get("optional-dependencies", {}))
pip("-e", f".[{extra}]" if extra else ".")
group = pick(meta.get("dependency-groups", {}))
if group:
    pip("--group", group)
for f in ("requirements/test.txt", "requirements/tests.txt", "requirements-test.txt",
          "requirements-tests.txt", "test-requirements.txt", "requirements-dev.txt", "requirements/dev.txt"):
    if os.path.exists(f):
        pip("-r", f)
pip("pytest")
'''


# ---- pure helpers ----------------------------------------------------------------

def parse_spec(spec: str) -> tuple[str, int, int | None]:
    """'owner/repo#123' or 'owner/repo#123:120' -> (repo, pull request, issue or None)."""
    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)(?::(\d+))?", spec)
    if not m:
        raise ValueError(f"expected owner/repo#PR or owner/repo#PR:ISSUE, got {spec!r}")
    return m[1], int(m[2]), int(m[3]) if m[3] else None


def issue_candidates(title: str, body: str) -> list[int]:
    """Issue numbers a pull request refers to, most likely first: closing keywords, then
    'issue 123' in the title or body, then any #123."""
    text = f"{title}\n{body}"
    found = re.findall(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", text)
    found += re.findall(r"(?i)\bissue\s*#?(\d+)", text) + re.findall(r"#(\d+)", text)
    return list(dict.fromkeys(int(n) for n in found))


def parse_pytest(output: str) -> dict[str, str]:
    """Test id -> outcome, from the summary `pytest -rA` prints. Colour codes are removed first:
    some projects force colour in their pytest settings. A test id may contain spaces and " - "
    inside its [parameters]; the message after the id starts with " - "."""
    results = {}
    for line in re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR|XFAIL|XPASS) (\S+?(?:\[.*?\])?)(?= - |\s*$)", line)
        if m:
            results[m[2]] = m[1]
    return results


def split_tests(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """FAIL_TO_PASS and PASS_TO_PASS, from the outcomes without and with the code change."""
    fixed = sorted(t for t, s in after.items() if s in PASSING and before.get(t) not in PASSING)
    kept = sorted(t for t, s in after.items() if s in PASSING and before.get(t) in PASSING)
    return fixed, kept


def test_modules(files: list[str]) -> list[str]:
    """The changed files pytest should run: test modules, not helpers or data."""
    return [f for f in files if f.endswith(".py") and re.match(r"(test_.*|.*_test|tests?)\.py$", PurePosixPath(f).name)]


def dockerfile(repo: str, merge_commit: str, python: str, install: str | None) -> str:
    script = base64.b64encode(INSTALL.encode()).decode()
    setup = install or f"echo {script} | base64 -d > /tmp/install.py && python /tmp/install.py"
    return (f"FROM python:{python}\n"
            f"RUN git clone --quiet https://github.com/{repo}.git /testbed && "
            f"git -C /testbed checkout --quiet {merge_commit}^1\n"
            "WORKDIR /testbed\n"
            f"RUN pip install --quiet --upgrade pip && {setup}\n")


# ---- GitHub and Docker -------------------------------------------------------------

def github(path: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "repo-bug-hunter"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = urllib.request.Request(f"{API}/{path}", headers=headers)
        with urllib.request.urlopen(request, timeout=30, context=TLS) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429) and e.headers.get("x-ratelimit-remaining") == "0":
            wait = int(e.headers.get("x-ratelimit-reset", time.time())) - time.time()
            raise RuntimeError(f"GitHub's API limit for this network is used up; it resets in about "
                               f"{max(1, round(wait / 60))} minutes. Set GITHUB_TOKEN (a token with no scopes "
                               "is enough, from https://github.com/settings/tokens) for 5,000 requests an hour"
                               ) from None
        raise


def find_issue(repo: str, pr: dict, number: int | None) -> dict:
    for n in [number] if number else issue_candidates(pr["title"], pr.get("body") or ""):
        if n == pr["number"]:
            continue
        issue = github(f"repos/{repo}/issues/{n}")
        if "pull_request" not in issue:
            return issue
    raise ValueError(f"{repo}#{pr['number']} names no issue. Pass the issue as {repo}#{pr['number']}:ISSUE; "
                     "the pull request's own description would give the fix away")


def build_image(tag: str, recipe: str) -> None:
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0:
        return
    print(f"building {tag} (cloning and installing the project; a few minutes the first time)", flush=True)
    p = subprocess.run(["docker", "build", "-t", tag, "-"], input=recipe.encode(), capture_output=True, timeout=3600)
    if p.returncode != 0:
        raise RuntimeError(f"docker build failed:\n{p.stderr.decode(errors='replace')[-3000:]}")


def run_tests(env, files: list[str]) -> dict[str, str]:
    # No bytecode: after `git apply` makes a same-size edit within the second, Python would run
    # the stale .pyc of the old code (see tools.Toolbox._write).
    env.run("find . -name __pycache__ -type d -prune -exec rm -rf {} +", timeout=120)
    _, out = env.run(f"PYTHONDONTWRITEBYTECODE=1 {PYTEST} {quote(*files)}", timeout=900)
    return parse_pytest(out)


def apply(env, patch: str, name: str) -> bool:
    path = f"{PurePosixPath(env.workdir).parent}/repo-bug-hunter-{name}.diff"  # beside the repository, not in it
    env.write(path, patch.encode())
    return env.run(f"git apply {quote(path)}", timeout=60)[0] == 0


def prepare(repo: str, number: int, issue_number: int | None, python: str, install: str | None) -> dict:
    """A SWE-bench-style task for one merged pull request: issue text, image, patches and tests."""
    pr = github(f"repos/{repo}/pulls/{number}")
    if not pr.get("merged_at"):
        raise ValueError(f"{repo}#{number} isn't merged")
    issue = find_issue(repo, pr, issue_number)
    owner, name = repo.split("/")
    iid = f"{owner}__{name}-{number}"
    tag = f"repo-bug-hunter-pr/{iid}".lower()
    merge = pr["merge_commit_sha"]
    if not re.fullmatch(r"[0-9a-f]{40,64}", merge or ""):  # it goes into a Dockerfile and git commands
        raise ValueError(f"{repo}#{number}: GitHub gave no merge commit for it")
    build_image(tag, dockerfile(repo, merge, python, install))
    with DockerEnv(tag, platform=None) as env:
        before = env.run("git rev-parse HEAD", timeout=30)[1].strip()
        files = env.run(f"git diff --name-only {before} {merge}", timeout=60)[1].split()
        if len(files) != pr["changed_files"]:
            raise ValueError(f"{repo}#{number}: the merge commit changes {len(files)} files but the pull request "
                             f"{pr['changed_files']}; it may have been rebase-merged, which isn't supported")
        tests = [f for f in files if is_test_path(f)]
        code = [f for f in files if f not in tests]
        modules = [f for f in test_modules(tests) if env.run(f"git cat-file -e {merge}:{quote(f)}", timeout=30)[0] == 0]
        if not code or not modules:
            raise ValueError(f"{repo}#{number} needs both a code change and test modules to grade with")
        test_patch = env.run(f"git diff {before} {merge} -- {quote(*tests)}", timeout=60)[1]
        gold_patch = env.run(f"git diff {before} {merge} -- {quote(*code)}", timeout=60)[1]
        if not apply(env, test_patch, "tests"):
            raise ValueError(f"{repo}#{number}: the fix's tests don't apply to the commit before it")
        without = run_tests(env, modules)
        if not apply(env, gold_patch, "fix"):
            raise ValueError(f"{repo}#{number}: the fix doesn't apply to the commit before it")
        fixed, kept = split_tests(without, run_tests(env, modules))
    if not fixed:
        raise ValueError(f"{repo}#{number}: no test fails without the fix and passes with it, so a patch "
                         "can't be graded (or the tests can't run in this environment)")
    return {"instance_id": iid, "repo": repo, "pull_request": number, "issue": issue["number"],
            "problem_statement": f"{issue['title']}\n\n{issue.get('body') or ''}".strip(),
            "image": tag, "base_commit": before, "merge_commit": merge, "patch": gold_patch,
            "test_patch": test_patch, "test_files": tests,
            "test_modules": modules, "FAIL_TO_PASS": fixed, "PASS_TO_PASS": kept}


def grade(task: dict, patch: str) -> dict:
    """Like the SWE-bench grader: the agent's patch, then the fix's tests, in a fresh container."""
    if not patch.strip():
        return {"resolved": False, "error": "empty_patch"}
    with DockerEnv(task["image"], platform=None) as env:
        if not apply(env, patch, "agent"):
            return {"resolved": False, "error": "patch_apply_failed"}
        # The fix's tests replace whatever the agent did to those files.
        existing = [f for f in task["test_files"]
                    if env.run(f"git cat-file -e {task['base_commit']}:{quote(f)}", timeout=30)[0] == 0]
        if existing:
            env.run(f"git checkout {task['base_commit']} -- {quote(*existing)}", timeout=60)
        if not apply(env, task["test_patch"], "tests"):
            return {"resolved": False, "error": "patch_apply_failed"}
        results = run_tests(env, task["test_modules"])

    def passed(test: str) -> bool:
        return results.get(test) in PASSING

    f2p, p2p = task["FAIL_TO_PASS"], task["PASS_TO_PASS"]
    error = None if results else "eval_error: the tests produced no results"
    return {"resolved": bool(results) and all(map(passed, f2p + p2p)), "error": error,
            "f2p_passed": [t for t in f2p if passed(t)], "f2p_failed": [t for t in f2p if not passed(t)],
            "p2p_passed": sum(map(passed, p2p)), "p2p_failed": [t for t in p2p if not passed(t)]}


# ---- the command -------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prs", nargs="+", help="merged pull requests: owner/repo#PR, or owner/repo#PR:ISSUE")
    ap.add_argument("--name", required=True, help="results go to runs/<name>/")
    ap.add_argument("--variant", choices=["baseline", "test_first"], default="baseline")
    add_model_args(ap)
    ap.add_argument("--python", default="3.12", help="Python version of the task images")
    ap.add_argument("--install", help="shell command that installs the project and its test "
                                      "dependencies in the image (default: detected from pyproject.toml)")
    ap.add_argument("--max-steps", type=int, default=50)
    ap.add_argument("--max-cost", type=float, default=2.0)
    ap.add_argument("--redo", action="store_true", help="run the agent again on tasks that already have a trajectory")
    args = ap.parse_args()
    try:
        specs = [parse_spec(s) for s in args.prs]
        model = resolve_model(args)
    except ValueError as e:
        ap.error(str(e))
    if not docker_available():
        ap.error("Docker is not running; start Docker Desktop (or Colima, OrbStack) and try again")
    if problem := check_model(model):
        ap.error(problem)

    out = RUNS / args.name
    (out / "trajs").mkdir(parents=True, exist_ok=True)
    (out / "tasks").mkdir(exist_ok=True)
    config = {"dataset": "github pull requests", "variant": args.variant, **model.config(),
              "difficulty": None, "max_steps": args.max_steps, "max_cost": args.max_cost, "seed": None,
              "instances": []}
    cfg_path, eval_path = out / "config.json", out / "eval.json"
    if cfg_path.exists():
        old = json.loads(cfg_path.read_text())
        if key := changed_setting(old, config):
            ap.error(f"{out} was run with {key}={old[key]!r}; use another --name")
        config["instances"] = old["instances"]
    evals = json.loads(eval_path.read_text()) if eval_path.exists() else {}
    test_first = args.variant == "test_first"
    query = None  # made on first use: a run that only re-reads results needs no model

    # Prepare every task first, so an unusable pull request is reported before any model call.
    tasks = []
    for repo, number, issue in specs:
        owner, name = repo.split("/")
        iid = f"{owner}__{name}-{number}"
        task_path = out / "tasks" / f"{iid}.json"
        try:
            if task_path.exists():
                task = json.loads(task_path.read_text())
            else:
                print(f"[{iid}] preparing: issue, image, and which tests the fix makes pass", flush=True)
                task = prepare(repo, number, issue, args.python, args.install)
                task_path.write_text(json.dumps(task, indent=1))
                print(f"[{iid}] issue #{task['issue']}: {len(task['FAIL_TO_PASS'])} tests to fix, "
                      f"{len(task['PASS_TO_PASS'])} to keep passing", flush=True)
        except (ValueError, RuntimeError, OSError) as e:
            print(f"[{iid}] skipped: {e}", flush=True)
            continue
        tasks.append(task)
        config["instances"] = sorted(set(config["instances"]) | {iid})
        cfg_path.write_text(json.dumps(config, indent=2))

    for task in tasks:
        iid, repo = task["instance_id"], task["repo"]
        traj_path = out / "trajs" / f"{iid}.json"
        if needs_run(traj_path, args.redo):
            print(f"[{iid}] starting", flush=True)
            query = query or make_query(model, system_prompt(test_first), tool_schemas(test_first))
            try:
                with DockerEnv(task["image"], platform=None) as env:
                    traj = run_agent(task, env, query, model=model.name, test_first=test_first,
                                     max_steps=args.max_steps, max_cost=args.max_cost,
                                     log=lambda s, iid=iid: print(f"[{iid}] {s}", flush=True))
            except Exception:  # one broken container must not end the whole run
                traj = error_traj(task, args.variant, model.name)
            traj["task"] = {"repo": repo, "difficulty": "unrated", "gold_files": patch_files(task["patch"])}
            traj_path.write_text(json.dumps(traj, indent=1, default=str))
            print(f"[{iid}] {traj['exit_status']} after {traj['n_steps']} steps, ${traj['cost']:.2f}", flush=True)
        traj = json.loads(traj_path.read_text())
        if traj["exit_status"] in RETRY:
            error = (traj.get("error") or "").strip().splitlines() or [""]
            print(f"[{iid}] not finished: {error[-1][:300]}", flush=True)  # a traceback's last line says what failed
            if hit_daily_limit(traj):
                break
            continue
        if iid not in evals or args.redo:
            try:
                evals[iid] = grade(task, traj["patch"])
            except Exception as e:  # not a result: graded on the next run
                print(f"[{iid}] not graded: {type(e).__name__}: {e}", flush=True)
                if evals.pop(iid, None) is not None:  # after --redo, that grade was for the old patch
                    eval_path.write_text(json.dumps(evals, indent=1))
                continue
            eval_path.write_text(json.dumps(evals, indent=1))
        print(f"[{iid}] {'RESOLVED' if evals[iid]['resolved'] else 'not resolved'}: "
              f"{len(evals[iid].get('f2p_passed', []))}/{len(task['FAIL_TO_PASS'])} tests to fix pass, "
              f"{len(evals[iid].get('p2p_failed', []))} previously passing tests broken", flush=True)

    write_preds(out)
    if not eval_path.exists():
        eval_path.write_text("{}")
    print(f"next: repo-bug-hunter analyze {out}")


if __name__ == "__main__":
    main()
