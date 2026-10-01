"""The command-line entry points: repo-bug-hunter <command>, smoke --local, doctor."""

import os
import sys
from collections import namedtuple

import pytest

from repo_bug_hunter import cli, doctor, smoke

from .conftest import response, tool_use

FIX = {"path": "stats.py", "old_str": "    return xs[len(xs) // 2]\n",
       "new_str": "    n = len(xs)\n    return (xs[n // 2 - 1] + xs[n // 2]) / 2 if n % 2 == 0 else xs[n // 2]\n"}


def test_cli_dispatches_to_each_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repo-bug-hunter"])
    cli.main()
    assert "repo-bug-hunter run" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["repo-bug-hunter", "analyze", "--help"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0 and "usage: repo-bug-hunter analyze" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["repo-bug-hunter", "nope"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "unknown command 'nope'" in str(e.value.code)


def test_local_commands_do_not_see_api_keys(repo, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("REPO_BUG_HUNTER_HARMLESS", "visible")
    code, out = repo.run("env")
    assert code == 0 and "sk-or-secret" not in out and "REPO_BUG_HUNTER_HARMLESS=visible" in out


def test_smoke_local_fixes_the_toy_bug_where_it_really_is(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("PATH", os.environ["PATH"])  # smoke adds a `python` shim; restore it afterwards
    monkeypatch.setattr(smoke.tempfile, "mkdtemp", lambda prefix: str(tmp_path))
    prompts = []

    def fake_model(model, system, tools):
        prompts.append(system)
        edit = response(tool_use("edit_file", **FIX))
        return lambda messages: edit if len(messages) == 1 else response(tool_use("submit"))
    monkeypatch.setattr(smoke, "make_query", fake_model)
    monkeypatch.setattr(sys, "argv", ["smoke", "--local", "--model", "claude-opus-5-5"])
    with pytest.raises(SystemExit) as e:
        smoke.main()
    out = capsys.readouterr().out
    assert e.value.code == 0 and "submitted in 2 steps" in out and "bug fixed: True" in out
    # The prompt names the directory the repository is really in, not /testbed.
    assert f"checked out at {(tmp_path / 'repo').resolve()} " in prompts[0] and "/testbed" not in prompts[0]
    assert (tmp_path / "trajectory.json").exists()


def test_smoke_needs_docker_unless_local(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(smoke, "docker_available", lambda: False)
    monkeypatch.setattr(sys, "argv", ["smoke", "--model", "claude-opus-5-5"])
    with pytest.raises(SystemExit) as e:
        smoke.main()
    assert e.value.code == 2 and "pass --local" in capsys.readouterr().err


def test_doctor(monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(doctor, "docker_available", lambda: True)
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: namedtuple("U", "free")(200e9))
    monkeypatch.setattr(doctor, "check_model", lambda m: None)
    monkeypatch.setattr(doctor, "openrouter_key", lambda key: {
        "is_free_tier": True, "free_model_daily_requests": {"used": 4, "limit": 50, "remaining": 46}})
    monkeypatch.setattr(doctor, "openrouter_models", lambda: [
        {"id": "a/b:free", "context_length": 9, "supported_parameters": ["tools"]}])
    monkeypatch.setattr(sys, "argv", ["doctor"])
    with pytest.raises(SystemExit) as e:
        doctor.main()
    out = capsys.readouterr().out
    assert e.value.code == 0 and "Ready." in out
    assert "46 of 50 left (buy $10 of credits" in out and "today: a/b:free" in out

    monkeypatch.setattr(doctor, "docker_available", lambda: False)
    with pytest.raises(SystemExit) as e:
        doctor.main()
    assert e.value.code == 1 and "FIX   Docker is not running" in capsys.readouterr().out
