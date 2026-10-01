import json

import pytest

from repo_bug_hunter.analyze import (
    bootstrap_delta,
    classify,
    compare,
    compare_md,
    load_run,
    mcnemar_exact,
    summary_md,
    wilson,
)
from repo_bug_hunter.viewer import build

PATCH = "diff --git a/pkg/core.py b/pkg/core.py\n--- a/pkg/core.py\n+++ b/pkg/core.py\n@@ -1 +1 @@\n-x\n+y\n"
OTHER = PATCH.replace("pkg/core.py", "pkg/other.py")


def traj(iid, status="submitted", patch=PATCH, cost=0.5, steps=10, **extra):
    return {"instance_id": iid, "variant": "baseline", "model": "claude-opus-5-5",
            "problem_statement": "bug", "exit_status": status, "patch": patch, "cost": cost,
            "n_steps": steps, "n_tool_calls": steps, "seconds": 60, "steps": [],
            "task": {"repo": "o/r", "difficulty": "<15 min fix", "gold_files": ["pkg/core.py"]}, **extra}


@pytest.mark.parametrize("t, ev, expected", [
    (traj("a"), {"resolved": True}, None),
    (traj("a", status="env_error", patch=""), {"resolved": False}, "run_error"),
    (traj("a", status="step_limit"), {"resolved": False}, "gave_up"),
    (traj("a", patch=""), {"resolved": False, "error": "empty_patch"}, "gave_up"),
    (traj("a"), {"resolved": False, "error": "patch_apply_failed"}, "patch_did_not_apply"),
    (traj("a"), {"resolved": False, "error": "test_timeout"}, "eval_error"),
    (traj("a", patch=OTHER), {"resolved": False, "p2p_failed": ["t"]}, "wrong_file"),
    (traj("a"), {"resolved": False, "f2p_failed": ["t1"], "p2p_failed": ["t2"]}, "broke_other_tests"),
    (traj("a"), {"resolved": False, "f2p_failed": ["t1"], "p2p_failed": []}, "tests_still_fail"),
])
def test_classify(t, ev, expected):
    assert classify(t, ev) == expected


def test_wilson():
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)
    assert wilson(0, 10)[0] == pytest.approx(0.0, abs=1e-12)
    assert wilson(0, 0) == (0.0, 0.0)


def test_mcnemar():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(1, 1) == 1.0
    assert mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert mcnemar_exact(3, 10) == mcnemar_exact(10, 3)


def test_bootstrap_delta():
    assert bootstrap_delta([1, 0, 1], [1, 0, 1]) == (0.0, 0.0)
    assert bootstrap_delta([0, 0, 0], [1, 1, 1]) == (1.0, 1.0)
    lo, hi = bootstrap_delta([0] * 50 + [1] * 50, [1] * 60 + [0] * 40)
    assert lo < 0.1 < hi


def write_run(root, name, trajs, evals, variant="baseline"):
    run = root / name
    (run / "trajs").mkdir(parents=True)
    for t in trajs:
        (run / "trajs" / f"{t['instance_id']}.json").write_text(json.dumps(t))
    (run / "eval.json").write_text(json.dumps(evals))
    (run / "config.json").write_text(json.dumps({
        "variant": variant, "model": "claude-opus-5-5", "effort": "medium", "max_steps": 50,
        "max_cost": 2.0, "seed": 0, "instances": [t["instance_id"] for t in trajs]}))
    return run


@pytest.fixture
def two_runs(tmp_path):
    ids = ["r__a-1", "r__a-2", "r__a-3", "r__a-4"]
    a = write_run(tmp_path, "baseline", [traj(i) for i in ids], {
        "r__a-1": {"resolved": True}, "r__a-2": {"resolved": False, "f2p_failed": ["t"]},
        "r__a-3": {"resolved": False, "f2p_failed": ["t"], "p2p_failed": ["u"]},
        "r__a-4": {"resolved": False, "f2p_failed": ["t"]}})
    b_trajs = [traj(i, cost=0.7, steps=14, repro={"status": "reproduced", "command": "python r.py",
                                                    "attempts": 0, "passed_at_submit": True})
               for i in ids]
    b = write_run(tmp_path, "test_first", b_trajs, {
        "r__a-1": {"resolved": True}, "r__a-2": {"resolved": True},
        "r__a-3": {"resolved": True}, "r__a-4": {"resolved": False, "f2p_failed": ["t"]}},
        variant="test_first")
    return a, b


def test_summary_and_comparison(two_runs):
    a, b = (load_run(r) for r in two_runs)
    md = summary_md(a)
    assert "| Resolved | 1/4 (25%" in md
    assert "| Cost per resolved issue | $2.00 |" in md
    assert "Broke other tests" in md and "Right file, but the issue's tests still fail | 2" in md

    c = compare(a, b)
    assert c["n"] == 4 and c["delta"] == pytest.approx(0.5)
    assert c["only_b"] == ["r__a-2", "r__a-3"] and c["only_a"] == []
    assert c["p_value"] == pytest.approx(0.5)
    text = compare_md(c)
    assert "+50.0 pts (too few tasks for an interval" in text and "could be noise" in text
    assert c["delta_ci"] is None

    assert "Test-first: did the reproduction help?" in summary_md(b)


def test_viewer_build(two_runs, tmp_path):
    out = tmp_path / "site"
    build(list(two_runs), out)
    manifest = json.loads((out / "data" / "manifest.json").read_text())
    assert [r["name"] for r in manifest["runs"]] == ["baseline", "test_first"]
    assert manifest["comparison"]["only_b"] == ["r__a-2", "r__a-3"]
    assert "instances" not in manifest["runs"][0]["config"]
    t = json.loads((out / "data" / "baseline" / "r__a-3.json").read_text())
    assert t["category"] == "broke_other_tests" and t["eval"]["p2p_failed"] == ["u"]
    assert (out / "index.html").read_text().startswith("<!doctype html>")


def test_unfinished_runs_are_not_scored(tmp_path):
    # A task stopped by the daily limit may even have a correct partial patch graded as resolved:
    # it is redone on resume, so it must count neither as a success nor as a failure yet.
    stopped = traj("r__a-2", status="api_error",
                   error="DailyLimitReached: daily request limit reached: free-models-per-day.")
    run = load_run(write_run(tmp_path, "free", [traj("r__a-1"), stopped], {
        "r__a-1": {"resolved": False, "f2p_failed": ["t"]}, "r__a-2": {"resolved": True}}))
    assert [r["id"] for r in run["rows"]] == ["r__a-1"]
    assert run["unfinished"] == [{"id": "r__a-2", "exit_status": "api_error", "daily_limit": True}]
    md = summary_md(run)
    assert "| Resolved | 0/1 (0%" in md
    assert "| Not finished | 1 (stopped at the daily request limit): not counted above." in md and "r__a-2 |" in md
    docker = load_run(write_run(tmp_path, "docker", [traj("r__a-1", status="env_error", patch="")], {}))
    assert "(stopped by Docker or API errors)" in summary_md(docker)
    assert "Not finished" not in summary_md(load_run(write_run(
        tmp_path, "full", [traj("r__a-1")], {"r__a-1": {"resolved": True}})))
