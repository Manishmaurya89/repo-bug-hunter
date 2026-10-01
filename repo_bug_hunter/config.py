"""Settings saved by `repo-bug-hunter setup`: the model to use and its API key.

They live in a JSON file only its owner can read, under $XDG_CONFIG_HOME (default ~/.config).
Environment variables take precedence over saved settings, and a model chosen on the command line
takes precedence over both.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

KEY_NAMES = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY")
MODEL_VARS = {"provider": "REPO_BUG_HUNTER_PROVIDER", "model": "REPO_BUG_HUNTER_MODEL",
              "base_url": "REPO_BUG_HUNTER_BASE_URL"}

from_file: set[str] = set()  # variables apply() filled in, so doctor can say where a key came from


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "repo-bug-hunter" / "config.json"


def load() -> dict:
    try:
        settings = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def save(settings: dict) -> Path:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.parent.chmod(0o700)
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # private from the first byte
    with os.fdopen(fd, "w") as f:
        json.dump(settings, f, indent=2)
    file.chmod(0o600)  # an existing file keeps its old mode through os.open
    return file


def forget() -> bool:
    try:
        path().unlink()
    except FileNotFoundError:
        return False
    return True


def apply(environ=os.environ) -> list[str]:
    """Set the saved keys and model choice in `environ` where they aren't set already."""
    settings = load()
    saved = {name: value for name, value in (settings.get("keys") or {}).items() if name in KEY_NAMES}
    saved |= {var: settings[field] for field, var in MODEL_VARS.items() if settings.get(field)}
    filled = [name for name, value in saved.items() if value and not environ.get(name)]
    for name in filled:
        environ[name] = saved[name]
    from_file.update(filled)
    return filled


def masked(key: str) -> str:
    return f"{key[:8]}…{key[-4:]}" if len(key) > 16 else "…"
