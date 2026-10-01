import json
import time
from types import SimpleNamespace as NS

import httpx2
import openai
import pytest

from repo_bug_hunter import openai_compat
from repo_bug_hunter.agent import cost_of, run_agent
from repo_bug_hunter.openai_compat import (ELIDED, RETRY_WAITS, DailyLimitReached, ProviderError, fit,
                                  from_openai, reply_tokens, to_openai)
from repo_bug_hunter.tools import Toolbox

from .conftest import text, thinking, tool_use


def reply(content="", calls=(), finish="tool_calls", extra=None, prompt=100, completion=20):
    tool_calls = [NS(id=f"call_{i}", function=NS(name=name, arguments=args))
                  for i, (name, args) in enumerate(calls)] or None
    msg = NS(content=content, tool_calls=tool_calls, model_extra=extra or {})
    usage = NS(prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=None)
    return NS(choices=[NS(message=msg, finish_reason=finish)], usage=usage)


def test_history_translates_to_openai_messages():
    history = [
        {"role": "user", "content": "<issue>bug</issue>"},
        {"role": "assistant", "content": [thinking("hmm"), text("Looking."),
                                          tool_use("read_file", id="a", path="x.py")]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "1 x",
                                      "is_error": False}]},
        {"role": "user", "content": "No tool was called."},
    ]
    assert to_openai("sys", history) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "<issue>bug</issue>"},
        {"role": "assistant", "content": "Looking.", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "x.py"}'}}]},
        {"role": "tool", "tool_call_id": "a", "content": "1 x"},
        {"role": "user", "content": "No tool was called."},
    ]


def test_reply_becomes_blocks_the_agent_loop_reads():
    r = from_openai(reply("", [("bash", '{"command": "ls"}')], extra={"reasoning": "list files"}), "m")
    assert [b.type for b in r.content] == ["thinking", "tool_use"]
    assert r.content[0].thinking == "list files"
    assert (r.content[1].name, r.content[1].input) == ("bash", {"command": "ls"})
    assert r.stop_reason == "tool_use" and r.model == "m"
    assert (r.usage.input_tokens, r.usage.output_tokens) == (100, 20)


def test_reply_edge_cases():
    # some servers finish with "stop" even when they called a tool
    assert from_openai(reply("", [("submit", "{}")], finish="stop"), "m").stop_reason == "tool_use"
    assert from_openai(reply("done", finish="stop"), "m").stop_reason == "end_turn"
    assert from_openai(reply("", finish="length"), "m").stop_reason == "max_tokens"
    inline = from_openai(reply("<think>plan</think>Answer", finish="stop"), "m")
    assert [(b.type, getattr(b, "thinking", getattr(b, "text", None))) for b in inline.content] == \
        [("thinking", "plan"), ("text", "Answer")]
    bad = from_openai(reply("", [("bash", '{"command": "ls"')]), "m").content[0]
    assert bad.input == {"_invalid_json": '{"command": "ls"'}


def test_invalid_json_arguments_get_a_useful_error(repo):
    out, err = Toolbox(repo).call("bash", {"_invalid_json": '{"command": "ls"'})
    assert err and "not valid JSON" in out


def test_fit_removes_oldest_outputs_in_chunks():
    chat = [{"role": "system", "content": "s"}, {"role": "user", "content": "issue"}]
    for i in range(20):
        chat.append({"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function",
                     "function": {"name": "write_file", "arguments": json.dumps({"path": "f", "content": "y" * 900})}}]})
        chat.append({"role": "tool", "tool_call_id": str(i), "content": "x" * 3000})
    assert fit(chat, 10**9) is chat
    fitted = fit(chat, 12_000)
    removed = [m["tool_call_id"] for m in fitted if m["role"] == "tool" and m["content"] == ELIDED]
    assert len(removed) % 6 == 0 and removed == [str(i) for i in range(len(removed))]
    assert sum(len(json.dumps(m)) for m in fitted) // 3 <= 12_000
    # file bodies in the removed turns' calls are dropped too; recent ones are kept
    first_call = json.loads(fitted[2]["tool_calls"][0]["function"]["arguments"])
    last_call = json.loads(fitted[-2]["tool_calls"][0]["function"]["arguments"])
    assert first_call["content"] == "[removed]" and last_call["content"] == "y" * 900
    # one more small turn does not move the cut: the prompt prefix stays the same
    longer = fit(chat + [{"role": "user", "content": "more"}], 12_000)
    assert longer[:len(fitted)] == fitted


def test_agent_runs_through_an_openai_compatible_server(repo, monkeypatch):
    sent = []

    class FakeClient:
        def __init__(self, **kw):
            self.chat = NS(completions=NS(create=self.create))

        def create(self, **kw):
            sent.append(kw)
            n = len(kw["messages"])
            if n == 2:
                return reply("", [("edit_file", json.dumps({"path": "calc.py", "old_str": "a - b",
                                                            "new_str": "a + b"}))], extra={"reasoning": "fix it"})
            return reply("", [("submit", "{}")])

    monkeypatch.setattr(openai_compat.openai, "OpenAI", FakeClient)
    query = openai_compat.openai_compatible("gemma4-32k", "http://localhost:11434/v1", "sys",
                                            [{"name": "t", "description": "d", "input_schema": {}}])
    traj = run_agent({"instance_id": "x", "repo": "r", "problem_statement": "bug"}, repo, query,
                     model="gemma4-32k")
    assert traj["exit_status"] == "submitted" and traj["cost"] == 0
    assert "+    return a + b" in traj["patch"]
    assert traj["steps"][0]["thinking"] == ["fix it"] and traj["fallback_used"] is False
    assert sent[0]["tools"][0]["function"]["name"] == "t" and sent[1]["messages"][-1]["role"] == "tool"


# ---- rate limits, daily caps and costs ------------------------------------------


def rate_limited(message, **headers):
    """A 429 shaped like OpenRouter's: the limit headers ride in the error's metadata."""
    body = {"message": message, "code": 429, "metadata": {"headers": headers}}
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    return openai.RateLimitError(f"Error code: 429 - {{'error': {body}}}", response=response, body=body)


def scripted_server(monkeypatch, outcomes):
    """An OpenAI-compatible client whose create() raises or returns `outcomes` in order."""
    calls, slept = [], []

    class FakeClient:
        def __init__(self, **kw):
            self.chat = NS(completions=NS(create=self.create))

        def create(self, **kw):
            calls.append(kw)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr(openai_compat.openai, "OpenAI", FakeClient)
    monkeypatch.setattr(openai_compat, "_sleep", slept.append)
    return calls, slept


def query_for(**kw):
    return openai_compat.openai_compatible("m", "https://openrouter.ai/api/v1", "sys", [], **kw)


def test_rate_limits_and_empty_replies_are_waited_out(monkeypatch):
    upstream = rate_limited("m is temporarily rate-limited upstream. Please retry shortly")
    empty = NS(choices=None, model_extra={"error": {"message": "Provider returned error"}})
    calls, slept = scripted_server(monkeypatch, [upstream, empty, reply("", [("submit", "{}")])])
    r = query_for()([{"role": "user", "content": "bug"}])
    assert r.content[0].name == "submit" and len(calls) == 3 and slept == list(RETRY_WAITS[:2])


def test_waiting_gives_up_eventually(monkeypatch):
    upstream = rate_limited("temporarily rate-limited upstream")
    calls, slept = scripted_server(monkeypatch, [upstream] * (len(RETRY_WAITS) + 1))
    with pytest.raises(openai.RateLimitError):
        query_for()([{"role": "user", "content": "bug"}])
    assert slept == list(RETRY_WAITS)


def test_a_daily_limit_stops_at_once(monkeypatch):
    reset = str(int((time.time() + 5 * 3600) * 1000))  # milliseconds, as OpenRouter sends it
    daily = rate_limited("Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free "
                         "model requests per day", **{"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})
    calls, slept = scripted_server(monkeypatch, [daily])
    with pytest.raises(DailyLimitReached, match=r"free-models-per-day.*resets in about 5\.0 hours"):
        query_for()([{"role": "user", "content": "bug"}])
    assert slept == []


def test_a_used_up_allowance_is_daily_only_if_it_resets_much_later():
    later = str(int((time.time() + 3 * 3600) * 1000))
    soon = str(int((time.time() + 30) * 1000))
    assert openai_compat.is_daily_limit(rate_limited("slow down", **{"X-RateLimit-Remaining": "0",
                                                                     "X-RateLimit-Reset": later}))
    assert not openai_compat.is_daily_limit(rate_limited("slow down", **{"X-RateLimit-Remaining": "0",
                                                                         "X-RateLimit-Reset": soon}))
    assert not openai_compat.is_daily_limit(rate_limited("free-models-per-min"))


def test_openrouter_reports_the_cost(monkeypatch):
    priced = reply("", [("submit", "{}")])
    priced.usage.cost = 0.0123
    calls, _ = scripted_server(monkeypatch, [priced])
    r = query_for(usage_accounting=True)([{"role": "user", "content": "bug"}])
    assert calls[0]["extra_body"] == {"usage": {"include": True}}
    assert r.usage.cost_usd == 0.0123 and cost_of("m", r.usage) == 0.0123
    assert from_openai(reply("", [("submit", "{}")]), "m").usage.cost_usd is None


def test_reply_room_grows_with_the_window():
    assert [reply_tokens(c) for c in (8192, 32_768, 65_536, 131_072, 1_000_000)] == [4096, 4096, 8192, 16_384, 16_384]


def test_empty_reply_is_a_provider_error():
    with pytest.raises(ProviderError, match="no reply"):
        from_openai(NS(choices=[], model_extra=None), "m")
