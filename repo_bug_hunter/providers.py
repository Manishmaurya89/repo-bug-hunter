"""Where the model comes from: OpenRouter (free models by default), Claude's own API, Ollama, or
any other OpenAI-compatible server.

    --model poolside/laguna-s-2.1:free          OpenRouter, the default; key in OPENROUTER_API_KEY
    --model claude-opus-5-5                     Claude's API; key in ANTHROPIC_API_KEY
    --provider ollama --model gemma4-32k        a local model in Ollama; no key
    --base-url URL --model NAME                 any other OpenAI-compatible server; key in LLM_API_KEY

The provider is inferred from the model name when it's clear: Claude model names go to Claude's
API, "vendor/model" names go to OpenRouter.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from dataclasses import dataclass

import certifi

from .agent import PRICES, claude
from .openai_compat import check_server, openai_compatible

# certifi's CA bundle: Python from python.org on macOS has no system certificates by default.
TLS = ssl.create_default_context(cafile=certifi.where())
OPENROUTER = "https://openrouter.ai/api/v1"
# Tested 2026-10-01. Free models come and go; `repo-bug-hunter doctor` lists today's.
FREE_MODEL = "poolside/laguna-s-2.1:free"
DEFAULT_CONTEXT = 32_768  # when the window can't be looked up; pass --context to override
MAX_CONTEXT = 131_072     # plan for at most this much prompt, even if a model allows more
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class Preset:
    base_url: str | None  # None: Claude's native API
    key_env: str | None   # environment variable that holds the API key
    default_model: str | None
    key_help: str = ""


PRESETS = {
    "openrouter": Preset(OPENROUTER, "OPENROUTER_API_KEY", FREE_MODEL,
                         "get a free key at https://openrouter.ai/settings/keys, then run `repo-bug-hunter setup`"),
    "anthropic": Preset(None, "ANTHROPIC_API_KEY", "claude-opus-5-5",
                        "get a key at https://platform.claude.com/settings/keys, then run `repo-bug-hunter setup`"),
    "ollama": Preset("http://localhost:11434/v1", None, None),
}


@dataclass
class Model:
    provider: str          # a PRESETS key, or "custom" for --base-url
    name: str
    base_url: str | None
    api_key: str | None
    context: int | None    # context window in tokens; filled in by check() on OpenRouter
    effort: str

    @property
    def native_claude(self) -> bool:
        return self.base_url is None

    def config(self) -> dict:
        """What a run records about its model."""
        return {"provider": self.provider, "model": self.name, "base_url": self.base_url,
                "effort": self.effort if self.native_claude else None, "context": self.context}


def add_model_args(ap) -> None:
    ap.add_argument("--model", help=f"model name (default: {FREE_MODEL}, free on OpenRouter)")
    ap.add_argument("--provider", choices=sorted(PRESETS),
                    help="where the model runs; inferred from --model when clear")
    ap.add_argument("--base-url", help="any other OpenAI-compatible server, e.g. http://localhost:1234/v1 "
                                       "for LM Studio; its key, if any, goes in LLM_API_KEY")
    ap.add_argument("--context", type=int,
                    help=f"the model's context window in tokens (looked up on OpenRouter, else {DEFAULT_CONTEXT})")
    ap.add_argument("--effort", default="medium", choices=EFFORTS, help="Claude only")


def resolve(args, need_key: bool = True) -> Model:
    """The model the command-line arguments ask for. Raises ValueError with a message for the user."""
    if not (args.model or args.provider or args.base_url):  # nothing chosen here: use what `setup` saved
        args.model = os.environ.get("REPO_BUG_HUNTER_MODEL")
        args.provider = os.environ.get("REPO_BUG_HUNTER_PROVIDER")
        args.base_url = os.environ.get("REPO_BUG_HUNTER_BASE_URL")
        if args.provider and args.provider not in PRESETS:
            raise ValueError(f"unknown provider {args.provider!r} in REPO_BUG_HUNTER_PROVIDER")
    if args.base_url:
        if not args.model:
            raise ValueError("--base-url needs --model")
        return Model("custom", args.model, args.base_url, os.environ.get("LLM_API_KEY"),
                     args.context or DEFAULT_CONTEXT, args.effort)
    provider = args.provider or _infer_provider(args.model)
    preset = PRESETS[provider]
    name = args.model or preset.default_model
    if not name:
        raise ValueError(f"pass --model with --provider {provider}")
    if provider == "anthropic" and name not in PRICES:
        raise ValueError(f"unknown Claude model {name!r}; known: {', '.join(sorted(PRICES))}")
    key = None
    if preset.key_env:
        key = os.environ.get(preset.key_env) or (os.environ.get("LLM_API_KEY") if provider == "openrouter" else None)
        if not key and need_key:
            raise ValueError(f"{name} needs an API key in {preset.key_env}: {preset.key_help}")
    context = args.context or (None if provider == "openrouter" else DEFAULT_CONTEXT)
    return Model(provider, name, preset.base_url, key, context, args.effort)


def _infer_provider(model: str | None) -> str:
    if model is None:
        return "openrouter"
    if model in PRICES:
        return "anthropic"
    if "/" in model:
        return "openrouter"
    raise ValueError(f"which provider serves {model!r}? Pass --provider (openrouter, anthropic, ollama) "
                     "or --base-url for another OpenAI-compatible server")


def check(m: Model) -> str | None:
    """None if the model can be used, else what is wrong. On OpenRouter this also fills in the
    model's context window."""
    if m.provider == "anthropic":
        return None
    if m.provider == "openrouter":
        return _check_openrouter(m)
    return check_server(m.base_url, m.name, m.api_key)


def _get(url: str, key: str | None = None) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    with urllib.request.urlopen(req, timeout=20, context=TLS) as r:
        return json.load(r)


def openrouter_models() -> list[dict]:
    return _get(f"{OPENROUTER}/models")["data"]


def free_tool_models(models: list[dict]) -> list[str]:
    """Free OpenRouter models that support tool calling, largest context first."""
    free = [m for m in models if m["id"].endswith(":free") and "tools" in (m.get("supported_parameters") or [])]
    return [m["id"] for m in sorted(free, key=lambda m: -(m.get("context_length") or 0))]


def openrouter_key(key: str) -> dict:
    """What OpenRouter knows about a key: free tier or not, and today's free-model requests."""
    return _get(f"{OPENROUTER}/key", key)["data"]


def _check_openrouter(m: Model) -> str | None:
    try:
        models = openrouter_models()
    except Exception as e:
        return f"cannot reach OpenRouter ({type(e).__name__}: {e})"
    found = next((x for x in models if x["id"] == m.name), None)
    suggest = ", ".join(free_tool_models(models)[:6])
    if found is None:
        return f"OpenRouter has no model {m.name!r}. Free models with tool calling today: {suggest}"
    if "tools" not in (found.get("supported_parameters") or []):
        return f"{m.name} doesn't support tool calling on OpenRouter, and the agent needs it. Try: {suggest}"
    if m.context is None:
        m.context = min(found.get("context_length") or DEFAULT_CONTEXT, MAX_CONTEXT)
    return None


def make_query(m: Model, system: str, tools: list[dict]):
    """query(messages) -> reply for this model; see agent.claude and openai_compat.openai_compatible."""
    if m.native_claude:
        return claude(m.name, m.effort, system, tools)
    return openai_compatible(m.name, m.base_url, system, tools, m.context or DEFAULT_CONTEXT,
                             api_key=m.api_key, usage_accounting=m.provider == "openrouter")
