"""Models behind an OpenAI-compatible chat API: OpenRouter (free models included), local models
(Ollama, LM Studio, vLLM) and other hosted providers.

The agent loop keeps its history in Anthropic's message shapes. This module translates that
history into OpenAI chat messages and wraps each reply in the shape run_agent reads, so the
loop, tools and analysis are identical whichever model runs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from types import SimpleNamespace

import openai

ELIDED = "[output removed to fit the context window; re-run the command if you need it]"
CHUNK = 6          # old outputs are removed this many at a time, so the prompt prefix stays stable
CHARS_PER_TOKEN = 3  # conservative estimate for code-heavy text
# Seconds between retries after a rate limit or an empty reply: about ten minutes in all.
RETRY_WAITS = (15, 30, 60, 60, 120, 120, 180)
_sleep = time.sleep  # replaced in tests


class DailyLimitReached(Exception):
    """The provider's daily request allowance is used up (OpenRouter free models: 50 or 1,000 a day).
    Waiting minutes won't help, so the run stops and resumes on the next invocation."""


class ProviderError(Exception):
    """The server answered without a reply, e.g. OpenRouter passing on an upstream failure."""


def reply_tokens(context: int) -> int:
    """Room for one reply, reasoning included: 4k in a small local window, up to 16k in a large one."""
    return max(4096, min(16_384, context // 8))


def openai_compatible(model: str, base_url: str, system: str, tools: list[dict],
                      context: int = 32_768, *, api_key: str | None = None,
                      usage_accounting: bool = False) -> Callable[[list], object]:
    """Returns query(messages) -> reply, like agent.claude(). `context` is the model's context
    window in tokens. Without `api_key`, the key comes from LLM_API_KEY. `usage_accounting` asks
    OpenRouter to report each request's cost, so --max-cost works for its paid models."""
    client = openai.OpenAI(base_url=base_url, api_key=api_key or os.environ.get("LLM_API_KEY", "unused"),
                           max_retries=2, timeout=1800)
    fn_tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in tools]
    max_tokens = reply_tokens(context)
    budget = context - max_tokens - 1000
    extra = {"extra_body": {"usage": {"include": True}}} if usage_accounting else {}

    def query(messages: list) -> object:
        chat = fit(to_openai(system, messages), budget)
        for wait in (*RETRY_WAITS, None):
            try:
                reply = client.chat.completions.create(model=model, messages=chat, tools=fn_tools,
                                                       max_tokens=max_tokens, **extra)
                return from_openai(reply, model)
            except openai.RateLimitError as e:
                if is_daily_limit(e):
                    raise DailyLimitReached(limit_message(e)) from None
                if wait is None:
                    raise
                why = "rate-limited"
            except ProviderError:
                if wait is None:
                    raise
                why = "no reply from the model server"
            print(f"{model}: {why}; trying again in {wait}s", file=sys.stderr, flush=True)
            _sleep(wait)

    return query


def _error_body(e: openai.APIStatusError) -> dict:
    # The SDK unwraps {"error": {...}}, so this is the error object itself.
    return e.body if isinstance(e.body, dict) else {}


def _limit_headers(e: openai.APIStatusError) -> dict:
    """Rate-limit headers, from the response or, on OpenRouter, from the error's metadata."""
    meta = _error_body(e).get("metadata") or {}
    headers = {k.lower(): str(v) for k, v in (meta.get("headers") or {}).items()}
    try:
        headers.update({k.lower(): v for k, v in e.response.headers.items()})
    except AttributeError:
        pass
    return headers


def _seconds_until_reset(e: openai.APIStatusError) -> float | None:
    reset = _limit_headers(e).get("x-ratelimit-reset")
    try:
        at = float(reset)
    except (TypeError, ValueError):
        return None
    if at > 1e12:  # OpenRouter gives a timestamp in milliseconds
        at /= 1000
    return at - time.time() if at > 1e9 else None


def is_daily_limit(e: openai.RateLimitError) -> bool:
    """OpenRouter names its free-model day cap in the error ("free-models-per-day"); otherwise a
    used-up allowance that resets more than 15 minutes from now counts as a daily limit."""
    if "per-day" in f"{e.message} {e.body}":
        return True
    wait = _seconds_until_reset(e)
    return _limit_headers(e).get("x-ratelimit-remaining") == "0" and wait is not None and wait > 15 * 60


def limit_message(e: openai.RateLimitError) -> str:
    text = _error_body(e).get("message") or e.message
    wait = _seconds_until_reset(e)
    when = f" It resets in about {wait / 3600:.1f} hours." if wait and wait > 0 else ""
    return f"daily request limit reached: {text}.{when} Run the same command again then to resume."


def check_server(base_url: str, model: str, api_key: str | None = None) -> str | None:
    """None if the server answers and serves `model`, else a message saying what is wrong."""
    client = openai.OpenAI(base_url=base_url, api_key=api_key or os.environ.get("LLM_API_KEY", "unused"),
                           max_retries=0, timeout=10)
    try:
        served = [m.id for m in client.models.list()]
    except Exception as e:
        return f"cannot reach a model server at {base_url} ({type(e).__name__}); is it running?"
    if served and model not in served and f"{model}:latest" not in served:
        shown = sorted(served)[:20]
        more = f" and {len(served) - len(shown)} more" if len(served) > len(shown) else ""
        return f"{base_url} has no model {model!r}; it serves: {', '.join(shown)}{more}"
    return None


def to_openai(system: str, messages: list) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "assistant":
            text = "".join(b.text for b in m["content"] if b.type == "text")
            calls = [{"id": b.id, "type": "function",
                      "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                     for b in m["content"] if b.type == "tool_use"]
            out.append({"role": "assistant", "content": text, **({"tool_calls": calls} if calls else {})})
        elif isinstance(m["content"], str):
            out.append({"role": "user", "content": m["content"]})
        else:
            out += [{"role": "tool", "tool_call_id": r["tool_use_id"], "content": r["content"]}
                    for r in m["content"]]
    return out


def _size(m: dict) -> int:
    return len(json.dumps(m)) // CHARS_PER_TOKEN


def fit(chat: list[dict], budget: int) -> list[dict]:
    """Remove the oldest tool outputs until the prompt fits. Removal goes in chunks of CHUNK,
    so between chunks every request starts with the same prefix and the server can reuse its cache."""
    total = sum(_size(m) for m in chat)
    if total <= budget:
        return chat
    outputs = [i for i, m in enumerate(chat) if m["role"] == "tool"]
    k, saved = 0, 0
    while k < len(outputs) and total - saved > budget:
        saved += _size(chat[outputs[k]]) - _size({"content": ELIDED})
        k += 1
    k = min(len(outputs), -(-k // CHUNK) * CHUNK)
    chat = list(chat)
    for i in outputs[:k]:
        chat[i] = {**chat[i], "content": ELIDED}
    last = outputs[k - 1] if k else 0
    for i, m in enumerate(chat[:last]):  # file bodies in old edit/write calls go too
        if m.get("tool_calls"):
            chat[i] = {**m, "tool_calls": [_shorten_call(c) for c in m["tool_calls"]]}
    return chat


def _shorten_call(call: dict) -> dict:
    args = json.loads(call["function"]["arguments"])
    args = {k: v if len(str(v)) < 300 else "[removed]" for k, v in args.items()}
    return {**call, "function": {**call["function"], "arguments": json.dumps(args)}}


def from_openai(reply, model: str) -> SimpleNamespace:
    if not getattr(reply, "choices", None):  # e.g. OpenRouter relaying an upstream error with status 200
        error = (getattr(reply, "model_extra", None) or {}).get("error")
        raise ProviderError(f"the server sent no reply: {error or reply}")
    choice = reply.choices[0]
    msg = choice.message
    extra = msg.model_extra or {}
    reasoning = extra.get("reasoning") or extra.get("reasoning_content") or ""
    text = msg.content or ""
    inline = re.match(r"\s*<think>(.*?)</think>(.*)", text, re.S)  # some models inline their reasoning
    if inline:
        reasoning, text = reasoning + inline.group(1), inline.group(2)

    blocks = []
    if reasoning.strip():
        blocks.append(SimpleNamespace(type="thinking", thinking=reasoning.strip()))
    if text.strip():
        blocks.append(SimpleNamespace(type="text", text=text))
    for n, call in enumerate(msg.tool_calls or []):
        try:
            args = json.loads(call.function.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            args = {"_invalid_json": call.function.arguments}
        blocks.append(SimpleNamespace(type="tool_use", id=call.id or f"call_{n}",
                                      name=call.function.name, input=args))

    stop = {"length": "max_tokens", "content_filter": "refusal"}.get(choice.finish_reason, "end_turn")
    if msg.tool_calls and stop == "end_turn":
        stop = "tool_use"
    u = reply.usage
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0 if u else 0
    cost = getattr(u, "cost", None) if u else None  # OpenRouter's usage accounting, in USD
    usage = SimpleNamespace(input_tokens=(u.prompt_tokens - cached) if u else 0,
                            output_tokens=u.completion_tokens if u else 0,
                            cache_read_input_tokens=cached, cache_creation_input_tokens=0,
                            cost_usd=float(cost) if cost is not None else None)
    return SimpleNamespace(content=blocks, stop_reason=stop, usage=usage, model=model)
