"""repo-bug-hunter pr: building a task from a merged pull request and grading against its tests.
Docker and GitHub are replaced by local git repositories and canned API answers."""

import base64
import json
import subprocess
import sys

import pytest

from repo_bug_hunter import analyze, pr
from repo_bug_hunter.env import LocalEnv

from .conftest import FakeQuery, response, tool_use

BUGGY = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
TESTS_BEFORE = "from calc import add, mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
TESTS_AFTER = TESTS_BEFORE + "\n\ndef test_add():\n    assert add(2, 2) == 4\n"


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """A project whose history has the bug, then the merged fix with a new test."""
    root = tmp_path / "upstream"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(BUGGY)
    (root / "tests" / "test_calc.py").write_text(TESTS_BEFORE)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "before")
    (root / "calc.py").write_text(BUGGY.replace("a - b", "a + b"))
    (root / "tests" / "test_calc.py").write_text(TESTS_AFTER)
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "Fix add (#7)")
    return root, git(root, "rev-parse", "HEAD")


@pytest.fixture
def fake_github_and_docker(upstream, tmp_path, monkeypatch):
    root, merge = upstream
    answers = {
        "repos/o/calc/pulls/7": {"number": 7, "title": "Fix add", "body": "See #6. Fixes #5.",
                                 "merged_at": "2026-09-30T00:00:00Z", "merge_commit_sha": merge,
                                 "changed_files": 2},
        "repos/o/calc/issues/6": {"number": 6, "title": "a pull request", "pull_request": {}},
        "repos/o/calc/issues/5": {"number": 5, "title": "add is wrong", "body": "add(2, 2) returns 0."},
    }
    monkeypatch.setattr(pr, "github", lambda path: answers[path])
    monkeypatch.setattr(pr, "build_image", lambda tag, recipe: None)
    counter = iter(range(100))

    def container(tag, platform=None):  # a fresh checkout of the commit before the fix, like a new container
        work = tmp_path / "containers" / str(next(counter)) / "testbed"
        subprocess.run(["git", "clone", "-q", str(root), str(work)], check=True)
        git(work, "checkout", "-q", f"{merge}^1")
        return LocalEnv(work)
    monkeypatch.setattr(pr, "DockerEnv", container)
    return merge


def test_pure_helpers():
    assert pr.parse_spec("python-humanize/humanize#334") == ("python-humanize/humanize", 334, None)
    assert pr.parse_spec("o/r#12:9") == ("o/r", 12, 9)
    with pytest.raises(ValueError):
        pr.parse_spec("humanize#334")
    assert pr.issue_candidates("Issue 1304: ichunked", "Closes #1310, see #3") == [1310, 1304, 3]
    out = ("PASSED tests/t.py::test_a\nFAILED tests/t.py::test_b - assert 1 == 2\n"
           "ERROR tests/u.py - ImportError: x\nXFAIL tests/t.py::test_c - flaky\n")
    assert pr.parse_pytest(out) == {"tests/t.py::test_a": "PASSED", "tests/t.py::test_b": "FAILED",
                                    "tests/u.py": "ERROR", "tests/t.py::test_c": "XFAIL"}
    # Real projects: forced colour (humanize), and parameters with spaces and " - " in the id.
    coloured = ("\x1b[32mPASSED\x1b[0m tests/t.py::\x1b[1mtest_p[4 hours, 30 seconds]\x1b[0m\n"
                "FAILED tests/t.py::test_q[a - b] - AssertionError: x - y\n")
    assert pr.parse_pytest(coloured) == {"tests/t.py::test_p[4 hours, 30 seconds]": "PASSED",
                                         "tests/t.py::test_q[a - b]": "FAILED"}
    assert pr.split_tests({"a": "PASSED", "b": "FAILED"}, {"a": "PASSED", "b": "PASSED", "c": "PASSED",
                                                          "d": "FAILED"}) == (["b", "c"], ["a"])
    assert pr.test_modules(["tests/test_x.py", "tests/conftest.py", "tests/data/x.json", "pkg/y_test.py"]) == \
        ["tests/test_x.py", "pkg/y_test.py"]
    recipe = pr.dockerfile("o/r", "abc123", "3.12", None)
    assert "FROM python:3.12" in recipe and "checkout --quiet abc123^1" in recipe
    script = recipe.split("echo ")[1].split(" |")[0]
    assert "dependency-groups" in base64.b64decode(script).decode()
    recipe = pr.dockerfile("o/r", "a", "3.12", "make test-deps")
    assert "RUN pip install --quiet --upgrade pip && make test-deps" in recipe


def test_prepare_builds_a_task_from_a_merged_pull_request(fake_github_and_docker):
    task = pr.prepare("o/calc", 7, None, "3.12", None)
    assert task["instance_id"] == "o__calc-7" and task["issue"] == 5  # #6 is a pull request, skipped
    assert task["problem_statement"] == "add is wrong\n\nadd(2, 2) returns 0."
    assert task["FAIL_TO_PASS"] == ["tests/test_calc.py::test_add"]
    assert task["PASS_TO_PASS"] == ["tests/test_calc.py::test_mul"]
    assert task["test_files"] == task["test_modules"] == ["tests/test_calc.py"]
    assert "a + b" in task["patch"] and "test_add" in task["test_patch"] and "test_add" not in task["patch"]


def test_grade_uses_the_fixs_tests(fake_github_and_docker):
    task = pr.prepare("o/calc", 7, None, "3.12", None)
    fix = task["patch"]  # the real fix, as `git diff` wrote it
    assert pr.grade(task, fix) == {"resolved": True, "error": None, "f2p_passed": ["tests/test_calc.py::test_add"],
                                   "f2p_failed": [], "p2p_passed": 1, "p2p_failed": []}
    wrong = fix.replace("+    return a + b", "+    return 0")
    assert pr.grade(task, wrong)["f2p_failed"] == ["tests/test_calc.py::test_add"]
    assert pr.grade(task, "") == {"resolved": False, "error": "empty_patch"}
    assert pr.grade(task, "not a diff\n")["error"] == "patch_apply_failed"
    # The agent's own edits to the fix's test files are replaced by the real tests.
    with_tests = fix + ("diff --git a/tests/test_calc.py b/tests/test_calc.py\n--- a/tests/test_calc.py\n"
                        "+++ b/tests/test_calc.py\n@@ -1,4 +1,4 @@\n-from calc import add, mul\n"
                        "+from calc import add, mul  # edited\n \n \n def test_mul():\n")
    assert pr.grade(task, with_tests)["resolved"] is True


def test_a_pull_request_without_an_issue_is_refused(fake_github_and_docker, monkeypatch):
    monkeypatch.setattr(pr, "github", lambda path: {"number": 7, "title": "Fix add", "body": "No issue."})
    with pytest.raises(ValueError, match="names no issue"):
        pr.find_issue("o/calc", pr.github("x"), None)


def test_the_command_runs_the_agent_and_grades_it(fake_github_and_docker, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(pr, "docker_available", lambda: True)
    edit = response(tool_use("edit_file", path="calc.py", old_str="a - b", new_str="a + b"))
    monkeypatch.setattr(pr, "make_query", lambda *a: FakeQuery(edit, response(tool_use("submit"))))
    monkeypatch.setattr(sys, "argv", ["pr", "o/calc#7", "--name", "fresh", "--model", "claude-opus-5-5"])
    pr.main()
    out = capsys.readouterr().out
    assert "[o__calc-7] RESOLVED: 1/1 tests to fix pass" in out
    run = analyze.load_run(tmp_path / "runs" / "fresh")
    assert [(r["id"], r["resolved"], r["gold_files"]) for r in run["rows"]] == [("o__calc-7", True, ["calc.py"])]
    assert json.loads((tmp_path / "runs" / "fresh" / "config.json").read_text())["instances"] == ["o__calc-7"]
    # Running it again reuses the task and the result: no new agent run, no new grading.
    monkeypatch.setattr(pr, "make_query", lambda *a: pytest.fail("the agent ran again"))
    monkeypatch.setattr(pr, "grade", lambda *a: pytest.fail("graded again"))
    monkeypatch.setattr(pr, "prepare", lambda *a: pytest.fail("prepared again"))
    pr.main()


def test_a_used_up_github_limit_says_what_to_do(monkeypatch):
    import email.message
    import urllib.error

    headers = email.message.Message()
    headers["x-ratelimit-remaining"] = "0"
    headers["x-ratelimit-reset"] = str(int(__import__("time").time()) + 38 * 60)

    def limited(request, timeout, context):
        assert context is pr.TLS  # certifi's certificates, not the system's
        raise urllib.error.HTTPError(request.full_url, 403, "rate limit exceeded", headers, None)
    monkeypatch.setattr(pr.urllib.request, "urlopen", limited)
    with pytest.raises(RuntimeError, match=r"resets in about 3[78] minutes\. Set GITHUB_TOKEN"):
        pr.github("repos/o/r/pulls/1")


def test_a_pull_request_without_a_merge_commit_is_refused(fake_github_and_docker, monkeypatch):
    real = pr.github
    monkeypatch.setattr(pr, "github", lambda path: {**real(path), "merge_commit_sha": "main; rm -rf /"}
                        if path.endswith("/pulls/7") else real(path))
    with pytest.raises(ValueError, match="no merge commit"):
        pr.prepare("o/calc", 7, None, "3.12", None)


def test_crashes_are_reported_and_retried_on_the_next_run(fake_github_and_docker, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(pr, "docker_available", lambda: True)
    edit = response(tool_use("edit_file", path="calc.py", old_str="a - b", new_str="a + b"))
    monkeypatch.setattr(pr, "make_query", lambda *a: FakeQuery(edit, response(tool_use("submit"))))
    monkeypatch.setattr(sys, "argv", ["pr", "o/calc#7", "--name", "fresh", "--model", "claude-opus-5-5"])
    run_agent, grade = pr.run_agent, pr.grade

    def crash(*args, **kwargs):
        raise RuntimeError("docker run failed: no space left on device")
    monkeypatch.setattr(pr, "run_agent", crash)
    pr.main()
    out = capsys.readouterr().out
    assert "[o__calc-7] not finished: RuntimeError: docker run failed: no space left on device" in out

    monkeypatch.setattr(pr, "run_agent", run_agent)
    monkeypatch.setattr(pr, "grade", crash)
    pr.main()
    assert "[o__calc-7] not graded: RuntimeError: docker run failed" in capsys.readouterr().out

    monkeypatch.setattr(pr, "grade", grade)
    pr.main()
    assert "[o__calc-7] RESOLVED" in capsys.readouterr().out
