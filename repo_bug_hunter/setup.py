"""Choose a model and save its API key once; every command then uses them.

    repo-bug-hunter setup                 # asks for the provider, the model and the key
    repo-bug-hunter setup --show          # what is saved, with keys masked
    repo-bug-hunter setup --forget        # delete the saved settings

The key is read without echo, or from standard input (setup --provider openrouter < key.txt), and
never from the command line, so it stays out of your shell history. It is saved in a file only you
can read. An environment variable such as OPENROUTER_API_KEY still takes precedence.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import urllib.error

import anthropic

from . import config
from .agent import PRICES
from .openai_compat import check_server
from .providers import FREE_MODEL, OPENROUTER, PRESETS, Model, openrouter_key
from .providers import check as check_model

LABELS = {"openrouter": "OpenRouter", "anthropic": "Anthropic", "custom": "Server"}
PROVIDERS = {  # name: (description, key variable, suggested model)
    "openrouter": ("OpenRouter: many models, some free; a free key works", "OPENROUTER_API_KEY", FREE_MODEL),
    "anthropic": ("Anthropic: Claude models; paid key", "ANTHROPIC_API_KEY", "claude-sonnet-5-5"),
    "ollama": ("Ollama: a model on this machine; no key", None, None),
    "custom": ("another OpenAI-compatible server (LM Studio, vLLM, ...)", "LLM_API_KEY", None),
}


def ask(question: str, default: str | None = None) -> str:
    if not sys.stdin.isatty():
        if default:
            return default
        raise SystemExit(f"setup: {question.lower()} is needed; pass it as an option when not typing interactively")
    answer = input(f"{question}{f' [{default}]' if default else ''}: ").strip()
    return answer or default or ask(question, default)


def ask_provider() -> str:
    if not sys.stdin.isatty():
        raise SystemExit("setup: pass --provider when not typing interactively")
    names = list(PROVIDERS)
    for n, name in enumerate(names, 1):
        print(f"  {n}. {PROVIDERS[name][0]}")
    choice = ask("Provider", "1")
    return names[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(names) else ask_provider()


def read_key(label: str, optional: bool = False) -> str | None:
    if sys.stdin.isatty():
        hint = "Enter to skip" if optional else "input is hidden"
        key = getpass.getpass(f"{label} API key ({hint}): ")
    else:
        key = sys.stdin.readline()
    key = key.strip()
    if not key and not optional:
        raise SystemExit("setup: no key given")
    return key or None


def check(provider: str, model: str, base_url: str | None, key: str | None) -> str | None:
    """None if the key works and the model exists, else what is wrong. No model is run."""
    if provider == "openrouter":
        if problem := check_model(Model("openrouter", model, OPENROUTER, key, None, "medium")):
            return problem + ("; save it anyway with --no-check" if problem.startswith("cannot reach") else "")
        try:
            openrouter_key(key)
        except urllib.error.HTTPError as e:
            return f"OpenRouter rejected this key (HTTP {e.code})"
        except OSError as e:
            return f"could not reach OpenRouter to check the key ({e}); save it anyway with --no-check"
        return None
    if provider == "anthropic":
        if model not in PRICES:
            return f"unknown Claude model {model!r}; known: {', '.join(sorted(PRICES))}"
        try:
            anthropic.Anthropic(api_key=key, max_retries=1).models.retrieve(model)
        except anthropic.AuthenticationError:
            return "Anthropic rejected this key"
        except anthropic.APIError as e:
            return f"could not check the key with Anthropic ({type(e).__name__}); save it anyway with --no-check"
        return None
    return check_server(base_url, model, key)


def show() -> None:
    settings = config.load()
    if not settings:
        print(f"Nothing saved yet ({config.path()}). Run `repo-bug-hunter setup`.")
        return
    print(f"Saved in {config.path()}:")
    for field in ("provider", "model", "base_url"):
        if settings.get(field):
            print(f"  {field}: {settings[field]}")
    for name, key in (settings.get("keys") or {}).items():
        override = "  (also set in your environment, which takes precedence)" if os.environ.get(name) else ""
        print(f"  {name}: {config.masked(key)}{override}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=list(PROVIDERS))
    ap.add_argument("--model", help="default: a suggestion for the provider")
    ap.add_argument("--base-url", help="the server's address, for --provider custom")
    ap.add_argument("--no-check", action="store_true", help="save without checking the key")
    ap.add_argument("--show", action="store_true", help="print the saved settings, keys masked")
    ap.add_argument("--forget", action="store_true", help="delete the saved settings")
    args = ap.parse_args()
    if args.show:
        return show()
    if args.forget:
        print(f"Deleted {config.path()}." if config.forget() else "Nothing was saved.")
        return

    provider = args.provider or ask_provider()
    _, key_name, suggested = PROVIDERS[provider]
    base_url = args.base_url or (PRESETS["ollama"].base_url if provider == "ollama" else None)
    if provider == "custom" and not base_url:
        base_url = ask("Server address, such as http://localhost:1234/v1")
    model = args.model or ask("Model", suggested)
    key = read_key(LABELS[provider], optional=provider == "custom") if key_name else None

    if not args.no_check:
        print("Checking...", flush=True)
        if problem := check(provider, model, base_url, key):
            raise SystemExit(f"setup: {problem}. Nothing was saved.")

    settings = config.load()
    settings.update(provider=None if provider == "custom" else provider, model=model,
                    base_url=base_url if provider == "custom" else None)
    settings = {k: v for k, v in settings.items() if v is not None}
    if key:
        settings.setdefault("keys", {})[key_name] = key
    file = config.save(settings)
    print(f"Saved {provider} / {model} to {file}, readable only by you.")
    print("Next: `repo-bug-hunter doctor` checks Docker and the rest.")
