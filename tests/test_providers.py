import argparse

import pytest

from repo_bug_hunter import providers
from repo_bug_hunter.providers import FREE_MODEL, MAX_CONTEXT, add_model_args, check, resolve


def parse(*argv):
    ap = argparse.ArgumentParser()
    add_model_args(ap)
    return ap.parse_args(argv)


@pytest.fixture
def keys(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_default_is_a_free_openrouter_model(keys):
    keys.setenv("OPENROUTER_API_KEY", "or-key")
    m = resolve(parse())
    assert (m.provider, m.name, m.api_key, m.context) == ("openrouter", FREE_MODEL, "or-key", None)
    assert m.base_url == providers.OPENROUTER and not m.native_claude


def test_provider_is_inferred_from_the_model_name(keys):
    keys.setenv("OPENROUTER_API_KEY", "k")
    keys.setenv("ANTHROPIC_API_KEY", "k")
    assert resolve(parse("--model", "claude-opus-5-5")).provider == "anthropic"
    assert resolve(parse("--model", "qwen/qwen3.8-27b:free")).provider == "openrouter"
    with pytest.raises(ValueError, match="which provider"):
        resolve(parse("--model", "gemma4-32k"))
    m = resolve(parse("--provider", "ollama", "--model", "gemma4-32k"))
    assert (m.base_url, m.api_key, m.context) == ("http://localhost:11434/v1", None, 32_768)
    with pytest.raises(ValueError, match="pass --model"):
        resolve(parse("--provider", "ollama"))


def test_a_missing_key_says_where_to_get_one(keys):
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY: get a key at https://openrouter.ai"):
        resolve(parse())
    assert resolve(parse(), need_key=False).api_key is None  # planning a run needs no key
    keys.setenv("LLM_API_KEY", "legacy")  # the variable earlier versions used
    assert resolve(parse()).api_key == "legacy"


def test_custom_servers_and_claude(keys):
    keys.setenv("LLM_API_KEY", "k")
    m = resolve(parse("--base-url", "http://localhost:1234/v1", "--model", "qwen", "--context", "8192"))
    assert (m.provider, m.api_key, m.context) == ("custom", "k", 8192)
    with pytest.raises(ValueError, match="needs --model"):
        resolve(parse("--base-url", "http://x/v1"))
    keys.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(ValueError, match="unknown Claude model"):
        resolve(parse("--provider", "anthropic", "--model", "claude-nope"))
    assert resolve(parse("--model", "claude-opus-5-5")).config()["effort"] == "medium"
    assert resolve(parse("--provider", "ollama", "--model", "m")).config()["effort"] is None


MODELS = [
    {"id": FREE_MODEL, "context_length": 262_144, "supported_parameters": ["tools", "max_tokens"]},
    {"id": "big/model:free", "context_length": 1_000_000, "supported_parameters": ["tools"]},
    {"id": "small/model:free", "context_length": 8_192, "supported_parameters": ["tools"]},
    {"id": "no-tools/model:free", "context_length": 8_192, "supported_parameters": ["max_tokens"]},
    {"id": "paid/model", "context_length": 8_192, "supported_parameters": ["tools"]},
]


def test_openrouter_check_finds_the_window_and_suggests_free_models(keys):
    keys.setattr(providers, "openrouter_models", lambda: MODELS)
    keys.setenv("OPENROUTER_API_KEY", "k")
    m = resolve(parse())
    assert check(m) is None and m.context == MAX_CONTEXT  # capped
    small = resolve(parse("--model", "small/model:free"))
    assert check(small) is None and small.context == 8192
    assert "doesn't support tool calling" in check(resolve(parse("--model", "no-tools/model:free")))
    missing = check(resolve(parse("--model", "gone/model:free")))
    assert "has no model" in missing and f"big/model:free, {FREE_MODEL}, small/model:free" in missing
    assert providers.free_tool_models(MODELS) == ["big/model:free", FREE_MODEL, "small/model:free"]


def test_make_query_asks_openrouter_for_costs(keys, monkeypatch):
    made = []
    monkeypatch.setattr(providers, "openai_compatible", lambda *a, **kw: made.append((a, kw)))
    monkeypatch.setattr(providers, "claude", lambda *a: made.append(("claude", a)))
    keys.setenv("OPENROUTER_API_KEY", "k")
    keys.setenv("ANTHROPIC_API_KEY", "k")
    m = resolve(parse("--context", "65536"))
    providers.make_query(m, "sys", [])
    (args, kw), = made
    assert args[1] == providers.OPENROUTER and args[4] == 65536
    assert kw == {"api_key": "k", "usage_accounting": True}
    providers.make_query(resolve(parse("--model", "claude-opus-5-5")), "sys", [])
    assert made[1] == ("claude", ("claude-opus-5-5", "medium", "sys", []))
