"""repo-bug-hunter setup: saving a model and key once, and every command using them."""

import argparse
import io
import stat
import sys
import urllib.error

import pytest

from repo_bug_hunter import config, setup
from repo_bug_hunter.providers import FREE_MODEL, add_model_args, resolve

KEY = "sk-or-v1-0123456789abcdef"


def run_setup(monkeypatch, *args, stdin=""):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(sys, "argv", ["setup", *args])
    setup.main()


def test_saved_settings_are_private_and_lose_to_the_environment():
    file = config.save({"provider": "openrouter", "keys": {"OPENROUTER_API_KEY": KEY, "SOMETHING_ELSE": "x"}})
    assert stat.S_IMODE(file.stat().st_mode) == 0o600 and stat.S_IMODE(file.parent.stat().st_mode) == 0o700
    environ = {"OPENROUTER_API_KEY": "from-the-shell"}
    assert config.apply(environ) == ["REPO_BUG_HUNTER_PROVIDER"]
    assert environ == {"OPENROUTER_API_KEY": "from-the-shell", "REPO_BUG_HUNTER_PROVIDER": "openrouter"}


def test_setup_checks_the_key_then_saves_it(monkeypatch, capsys):
    monkeypatch.setattr(setup, "check_model", lambda model: None)
    monkeypatch.setattr(setup, "openrouter_key", lambda key: {"label": "test"})
    run_setup(monkeypatch, "--provider", "openrouter", stdin=KEY + "\n")
    assert config.load() == {"provider": "openrouter", "model": FREE_MODEL, "keys": {"OPENROUTER_API_KEY": KEY}}
    out = capsys.readouterr().out
    assert "readable only by you" in out and KEY not in out


def test_a_rejected_key_is_not_saved(monkeypatch):
    def rejected(key):
        raise urllib.error.HTTPError("https://openrouter.ai/api/v1/key", 401, "no", None, None)
    monkeypatch.setattr(setup, "check_model", lambda model: None)
    monkeypatch.setattr(setup, "openrouter_key", rejected)
    with pytest.raises(SystemExit, match=r"rejected this key \(HTTP 401\)\. Nothing was saved"):
        run_setup(monkeypatch, "--provider", "openrouter", stdin=KEY + "\n")
    assert config.load() == {}


def test_the_saved_model_applies_unless_the_command_line_names_one(monkeypatch):
    config.save({"provider": "anthropic", "model": "claude-sonnet-5-5", "keys": {"ANTHROPIC_API_KEY": "sk-ant-x"}})
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    config.apply()
    ap = argparse.ArgumentParser()
    add_model_args(ap)
    saved = resolve(ap.parse_args([]))
    assert (saved.provider, saved.name, saved.api_key) == ("anthropic", "claude-sonnet-5-5", "sk-ant-x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    chosen = resolve(ap.parse_args(["--model", "a/b"]))
    assert (chosen.provider, chosen.name) == ("openrouter", "a/b")


def test_show_masks_keys_and_forget_deletes_them(monkeypatch, capsys):
    run_setup(monkeypatch, "--provider", "openrouter", "--no-check", stdin=KEY + "\n")
    capsys.readouterr()
    run_setup(monkeypatch, "--show")
    out = capsys.readouterr().out
    assert "sk-or-v1…cdef" in out and KEY not in out
    run_setup(monkeypatch, "--forget")
    assert config.load() == {} and "Deleted" in capsys.readouterr().out


def test_without_a_terminal_the_provider_must_be_given(monkeypatch):
    with pytest.raises(SystemExit, match="pass --provider"):
        run_setup(monkeypatch)
