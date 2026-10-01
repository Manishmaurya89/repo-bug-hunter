"""run -> evaluate -> analyze, with Docker, the API and the grader replaced by stand-ins."""

import json
import subprocess
import sys

import pytest

from repo_bug_hunter import analyze, evaluate, run
from repo_bug_hunter.env import LocalEnv
from repo_bug_hunter.openai_compat import DailyLimitReached

from .conftest import CALC, TEST, FakeQuery, response, tool_use

GOLD = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
TASKS = [{"instance_id": f"toy__calc-{i}", "repo": "toy/calc", "image": "unused",
          "problem_statement": "add is wrong", "patch": GOLD, "difficulty": "<15 min fix"}
         for i in (1, 2, 3)]


def fake_env(tmp_path):
    counter = iter(range(100))

    def make(image):
        root = tmp_path / "repos" / str(next(counter))
        (root / "tests").mkdir(parents=True)
        (root / "calc.py").write_text(CALC)
        (root / "tests" / "test_calc.py").write_text(TEST)
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):
            subprocess.run(["git", *cmd], cwd=root, check=True)
        return LocalEnv(root)
    return make


def offline(monkeypatch, tmp_path):
    """Claude with a fake key: resolving the model needs no network."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")


CLAUDE = ["--model", "claude-opus-5-5"]


def test_pipeline(tmp_path, monkeypatch):
    offline(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "docker_available", lambda: True)
    monkeypatch.setattr(run, "load_tasks", lambda: TASKS)
    monkeypatch.setattr(run, "DockerEnv", fake_env(tmp_path))
    edit = response(tool_use("edit_file", path="calc.py", old_str="a - b", new_str="a + b"))

    def fix(messages):  # stateless: one query function serves every task, like the real client
        return edit if len(messages) == 1 else response(tool_use("submit"))
    monkeypatch.setattr(run, "make_query", lambda *a: fix)
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "2", "--workers", "1", *CLAUDE])
    run.main()

    out = tmp_path / "runs" / "demo"
    config = json.loads((out / "config.json").read_text())
    assert len(config["instances"]) == 2 and config["variant"] == "baseline"
    preds = [json.loads(line) for line in (out / "preds.jsonl").read_text().splitlines()]
    assert len(preds) == 2 and all(p["model_name_or_path"] == "demo" for p in preds)
    assert all("+    return a + b" in p["model_patch"] for p in preds)

    # Re-running resumes: existing trajectories are kept, not recomputed.
    before = {p.name: p.stat().st_mtime_ns for p in (out / "trajs").iterdir()}
    run.main()
    assert {p.name: p.stat().st_mtime_ns for p in (out / "trajs").iterdir()} == before

    # Fake the harness output: one resolved, one whose patch did not apply.
    first, second = sorted(p["instance_id"] for p in preds)
    logs = tmp_path / "logs" / "run_evaluation" / "demo" / "demo"
    (logs / first).mkdir(parents=True)
    (logs / first / "report.json").write_text(json.dumps({first: {
        "patch_successfully_applied": True, "resolved": True,
        "tests_status": {"FAIL_TO_PASS": {"success": ["t"], "failure": []},
                         "PASS_TO_PASS": {"success": ["u"], "failure": []}}}}))
    (logs / second).mkdir(parents=True)
    (logs / second / "run_instance.log").write_text(">>>>> Patch Apply Failed:\nerror")
    (logs / second / "report.json").write_text("")  # what the Modal runner leaves on errors
    results = evaluate.collect(out)
    assert results[first]["resolved"] and results[first]["p2p_passed"] == 1
    assert results[second] == {"resolved": False, "error": "patch_apply_failed"}

    (out / "eval.json").write_text(json.dumps(results))
    rows = {r["id"]: r for r in analyze.load_run(out)["rows"]}
    assert rows[first]["category"] is None and rows[second]["category"] == "patch_did_not_apply"
    assert rows[first]["gold_files"] == ["calc.py"]


def test_run_refuses_to_mix_settings(tmp_path, monkeypatch):
    offline(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "docker_available", lambda: True)
    monkeypatch.setattr(run, "load_tasks", lambda: TASKS)
    monkeypatch.setattr(run, "DockerEnv", fake_env(tmp_path))
    monkeypatch.setattr(run, "make_query", lambda *a: FakeQuery(response(tool_use("submit"))))
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "1", "--workers", "1", *CLAUDE])
    run.main()
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "1", "--variant", "test_first", *CLAUDE])
    try:
        run.main()
        raise AssertionError("expected an error")
    except SystemExit as e:
        assert e.code == 2


def test_infrastructure_failures_are_retried_on_resume(tmp_path, monkeypatch):
    offline(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "docker_available", lambda: True)
    monkeypatch.setattr(run, "load_tasks", lambda: TASKS[:1])
    monkeypatch.setattr(run, "make_query", lambda *a: FakeQuery(response(tool_use("submit"))))
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "1", "--workers", "1", *CLAUDE])

    def broken(image):
        raise RuntimeError("Cannot connect to the Docker daemon")
    monkeypatch.setattr(run, "DockerEnv", broken)
    run.main()
    traj_path = tmp_path / "runs" / "demo" / "trajs" / "toy__calc-1.json"
    assert json.loads(traj_path.read_text())["exit_status"] == "env_error"

    monkeypatch.setattr(run, "DockerEnv", fake_env(tmp_path))  # Docker is back
    run.main()
    assert json.loads(traj_path.read_text())["exit_status"] == "submitted"


def test_run_stops_early_when_something_is_down(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")  # the default model; never contacted here
    monkeypatch.setattr(run, "load_tasks", lambda: TASKS)
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "1"])
    monkeypatch.setattr(run, "docker_available", lambda: False)
    try:
        run.main()
        raise AssertionError("expected an error")
    except SystemExit as e:
        assert e.code == 2
    assert "Docker is not running" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()

    monkeypatch.setattr(run, "docker_available", lambda: True)
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "1", "--model", "nope",
                                      "--base-url", "http://127.0.0.1:9/v1"])
    try:
        run.main()
        raise AssertionError("expected an error")
    except SystemExit as e:
        assert e.code == 2
    assert "cannot reach a model server" in capsys.readouterr().err


def test_a_daily_limit_stops_the_run_and_the_next_day_resumes_it(tmp_path, monkeypatch, capsys):
    offline(monkeypatch, tmp_path)
    monkeypatch.setattr(run, "docker_available", lambda: True)
    monkeypatch.setattr(run, "load_tasks", lambda: TASKS)
    monkeypatch.setattr(run, "DockerEnv", fake_env(tmp_path))

    def out_of_requests(messages):
        raise DailyLimitReached("daily request limit reached: free-models-per-day.")
    monkeypatch.setattr(run, "make_query", lambda *a: out_of_requests)
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "3", "--workers", "1", *CLAUDE])
    run.main()
    trajs = tmp_path / "runs" / "demo" / "trajs"
    assert [json.loads(p.read_text())["exit_status"] for p in trajs.iterdir()] == ["api_error"]
    assert "Stopped early" in capsys.readouterr().out  # the other two tasks never started
    assert (tmp_path / "runs" / "demo" / "preds.jsonl").read_text() == ""  # nothing unfinished is graded

    monkeypatch.setattr(run, "make_query", lambda *a: FakeQuery(response(tool_use("submit"))))
    run.main()
    assert sorted(json.loads(p.read_text())["exit_status"] for p in trajs.iterdir()) == ["submitted"] * 3
    assert len((tmp_path / "runs" / "demo" / "preds.jsonl").read_text().splitlines()) == 3


def test_print_instances_lists_what_still_needs_a_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # planning needs no key and no Docker
    monkeypatch.setattr(run, "docker_available", lambda: False)
    monkeypatch.setattr(run, "load_tasks", lambda: TASKS)
    trajs = tmp_path / "runs" / "demo" / "trajs"
    trajs.mkdir(parents=True)
    (trajs / "toy__calc-1.json").write_text(json.dumps({"exit_status": "submitted"}))
    (trajs / "toy__calc-2.json").write_text(json.dumps({"exit_status": "api_error"}))
    monkeypatch.setattr(sys, "argv", ["run", "--name", "demo", "--n", "3", "--print-instances", *CLAUDE])
    run.main()
    assert json.loads(capsys.readouterr().out) == ["toy__calc-2", "toy__calc-3"]
    assert not (tmp_path / "runs" / "demo" / "config.json").exists()


def test_evaluate_skips_the_grader_when_there_is_nothing_to_grade(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "runs" / "demo"
    run_dir.mkdir(parents=True)
    (run_dir / "preds.jsonl").write_text(json.dumps({"instance_id": "a", "model_name_or_path": "demo",
                                                     "model_patch": ""}) + "\n")
    monkeypatch.setattr(evaluate.subprocess, "run", lambda *a, **kw: pytest.fail("the grader ran"))
    monkeypatch.setattr(sys, "argv", ["evaluate", str(run_dir)])
    evaluate.main()
    assert json.loads((run_dir / "eval.json").read_text()) == {"a": {"resolved": False, "error": "empty_patch"}}
