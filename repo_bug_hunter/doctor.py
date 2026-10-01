"""Check that this machine and the model are ready, and say what to fix.

    repo-bug-hunter doctor                                   # the default free OpenRouter model
    repo-bug-hunter doctor --model claude-sonnet-5-5
    repo-bug-hunter doctor --provider ollama --model gemma4-32k
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys

from .env import docker_available
from .providers import (FREE_MODEL, add_model_args, free_tool_models, openrouter_key,
                        openrouter_models)
from .providers import check as check_model
from .providers import resolve as resolve_model

DISK_GB = 30  # a pilot of a few tasks; each task image is a few GB, and they share base layers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_model_args(ap)
    args = ap.parse_args()
    problems = 0

    def say(ok: bool | None, text: str) -> None:
        nonlocal problems
        problems += ok is False
        print(f"{'ok  ' if ok else 'note' if ok is None else 'FIX '}  {text}")

    say(sys.version_info >= (3, 10), f"Python {platform.python_version()}")
    if docker_available():
        say(True, "Docker is running")
    else:
        say(False, "Docker is not running. SWE-bench tasks need it: start Docker Desktop, Colima or "
                   "OrbStack, or run in GitHub Actions or Codespaces instead (see the README). "
                   "`repo-bug-hunter smoke --local` works without it.")
    if platform.machine().lower() in ("arm64", "aarch64"):
        say(None, "ARM machine: SWE-bench images are x86-64 and run under emulation, which is slower. "
                  "In Docker Desktop, turn on Rosetta for x86_64/amd64 emulation.")
    free = shutil.disk_usage(".").free / 1e9
    say(free >= DISK_GB, f"{free:.0f} GB free disk (a pilot needs about {DISK_GB} GB; all 500 tasks "
                         "need about 120 GB)")

    try:
        model = resolve_model(args)
    except ValueError as e:
        say(False, str(e))
        sys.exit(1)
    problem = check_model(model)
    say(problem is None, problem or f"{model.name} via {model.provider} is available"
        + (f", with a {model.context:,}-token window" if model.context else ""))

    if model.provider == "openrouter":
        try:
            key = openrouter_key(model.api_key)
        except Exception as e:
            say(False, f"OpenRouter rejected the key in OPENROUTER_API_KEY ({type(e).__name__}: {e})")
        else:
            daily = key.get("free_model_daily_requests") or {}
            if daily:
                say(None, f"free-model requests today: {daily.get('remaining')} of {daily.get('limit')} left"
                          + (" (buy $10 of credits once to raise the limit to 1,000 a day)"
                             if key.get("is_free_tier") else ""))
            if key.get("expires_at"):
                say(None, f"this key expires at {key['expires_at']}")
        if problem and model.name != FREE_MODEL:
            say(None, f"the tested default is {FREE_MODEL}")
        try:
            say(None, "free models with tool calling today: " + ", ".join(free_tool_models(openrouter_models())[:8]))
        except Exception:
            pass

    print("\nReady." if not problems else f"\n{problems} thing{'s' if problems > 1 else ''} to fix.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
