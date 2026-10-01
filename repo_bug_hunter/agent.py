"""The agent loop: ask Claude for tool calls, run them, repeat until submit or a limit."""

from __future__ import annotations

import time
from typing import Callable

import anthropic

from .tools import Toolbox

# USD per million tokens: input, output, 5-minute cache write, cache read.
# List prices as of September 2026; check https://claude.com/pricing before quoting costs.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
    "claude-fable-5-1": (10.00, 50.00, 12.50, 0.25),
    # Possible refusal-fallback targets, so a fallback turn is still priced.
    "claude-opus-5": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 6.25, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 2.50, 0.20),
}
# Models that take `fallbacks: "default"` (re-run a classifier refusal on another model).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

SYSTEM = """\
You are an autonomous software engineer fixing an issue in a Python repository. The repository \
is checked out at {workdir} with its dependencies installed. There is no network access, so tests \
that need the internet fail whatever you change.

Resolve the issue by changing the library's source code. Hidden tests written by the project's \
maintainers will check your change, so fix the underlying behavior the issue describes rather \
than special-casing its example. Changes to existing test files are discarded before grading.

Use the tools to explore the code, edit files, and run code and tests. Prefer edit_file and \
write_file over editing files with shell commands. Nobody will answer questions, so keep working \
until the fix is complete, then call submit."""

TEST_FIRST = """

Work test-first. Before changing any source file, write a test that reproduces the issue and \
fails on the current code. A standalone script such as {workdir}/repro_test.py that exits \
non-zero while the bug is present works for any project. Register it with record_failing_test, \
which runs it and checks that it fails; source files stay locked until then. When you call \
submit, the harness runs it again and it must pass."""


def system_prompt(test_first: bool, workdir: str = "/testbed") -> str:
    # The path must be where the repository really is: told /testbed while working elsewhere,
    # a model goes looking for it across the whole machine.
    return (SYSTEM + (TEST_FIRST if test_first else "")).format(workdir=workdir)


def task_prompt(task: dict) -> str:
    return f"<issue>\n{task['problem_statement'].strip()}\n</issue>\n\nRepository: {task['repo']}"


def cost_of(model: str, usage) -> float:
    # A provider that reports what a request cost (OpenRouter does) is taken at its word.
    reported = getattr(usage, "cost_usd", None)
    if reported is not None:
        return float(reported)
    # Models not in the table (local models, free tiers) are counted as free.
    p_in, p_out, p_write, p_read = PRICES.get(model, (0, 0, 0, 0))
    get = lambda f: getattr(usage, f, 0) or 0
    return (get("input_tokens") * p_in + get("output_tokens") * p_out
            + get("cache_creation_input_tokens") * p_write
            + get("cache_read_input_tokens") * p_read) / 1e6


def claude(model: str, effort: str, system: str, tools: list[dict]) -> Callable[[list], object]:
    """Returns query(messages) -> Message, with caching and fallbacks configured."""
    client = anthropic.Anthropic(max_retries=8)
    params = dict(model=model, max_tokens=64_000, system=system, tools=tools,
                  cache_control={"type": "ephemeral"})  # moves to the end of the history each turn
    betas = []
    if not model.startswith("claude-haiku"):  # Haiku 4.5 predates adaptive thinking and effort
        params["thinking"] = {"type": "adaptive", "display": "summarized"}
        params["output_config"] = {"effort": effort}
    if model in FALLBACK_MODELS:
        # A classifier refusal is re-run on another model. Steps record the serving model,
        # so a run that fell back is visible in the results.
        params["fallbacks"] = "default"
        betas.append("server-side-fallback-2026-07-01")

    def query(messages: list) -> object:
        with client.beta.messages.stream(messages=messages, betas=betas, **params) as stream:
            return stream.get_final_message()

    return query


def run_agent(task: dict, env, query: Callable[[list], object], *, model: str,
              test_first: bool = False, max_steps: int = 50, max_cost: float = 2.0,
              log: Callable[[str], None] = lambda s: None) -> dict:
    tools = Toolbox(env, test_first=test_first)
    messages: list = [{"role": "user", "content": task_prompt(task)}]
    traj = {"instance_id": task["instance_id"], "variant": "test_first" if test_first else "baseline",
            "model": model, "problem_statement": task["problem_statement"], "steps": [],
            "exit_status": "step_limit", "cost": 0.0}
    started = time.time()

    for i in range(1, max_steps + 1):
        try:
            resp = query(messages)
        except Exception as e:  # after the client's own retries; keep the trajectory so far
            traj["exit_status"], traj["error"] = "api_error", f"{type(e).__name__}: {e}"
            break
        served_by = resp.model if resp.model in PRICES else model
        step = {"i": i, "model": resp.model, "stop_reason": resp.stop_reason,
                "cost": cost_of(served_by, resp.usage), "thinking": [], "text": [], "actions": [],
                "usage": {k: getattr(resp.usage, k, 0) or 0 for k in (
                    "input_tokens", "output_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens")}}
        traj["steps"].append(step)
        traj["cost"] += step["cost"]
        # Append the content unchanged: thinking blocks must be passed back as-is.
        messages.append({"role": "assistant", "content": resp.content})

        for b in resp.content:
            if b.type == "thinking" and b.thinking:
                step["thinking"].append(b.thinking)
            elif b.type == "text" and b.text.strip():
                step["text"].append(b.text)
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            traj["exit_status"], traj["error"] = "refusal", str(getattr(details, "category", None))
            break

        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            messages.append({"role": "user", "content": "No tool was called, so nothing ran. Writing a "
                             "call out as text does not run it: use the tools to keep working, and call "
                             "submit when the fix is complete."})
            log(f"step {i} ${traj['cost']:.2f} (no tool call)")
            continue

        results = []
        for b in calls:
            t0 = time.time()
            if resp.stop_reason == "max_tokens":
                out, is_error = "The response hit max_tokens before this call was complete; it was not run.", True
            else:
                out, is_error = tools.call(b.name, b.input)
            step["actions"].append({"tool": b.name, "input": b.input, "output": out,
                                    "is_error": is_error, "seconds": round(time.time() - t0, 2)})
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out,
                            "is_error": is_error})
            log(f"step {i} ${traj['cost']:.2f} {b.name}: {_brief(b.input)}")
            if tools.submitted:
                break
        messages.append({"role": "user", "content": results})

        if tools.submitted:
            traj["exit_status"] = "submitted"
            break
        if traj["cost"] >= max_cost:
            traj["exit_status"] = "cost_limit"
            break

    traj["patch"] = tools.patch()
    traj["n_steps"] = len(traj["steps"])
    traj["n_tool_calls"] = sum(len(s["actions"]) for s in traj["steps"])
    traj["seconds"] = round(time.time() - started, 1)
    traj["fallback_used"] = any(s["model"] != model for s in traj["steps"])
    if test_first:
        traj["repro"] = {"status": tools.repro_status, "command": tools.repro_command,
                         "attempts": tools.repro_attempts,
                         "passed_at_submit": tools.repro_passed_at_submit}
    return traj


def _brief(args: dict) -> str:
    first = next(iter(args.values()), "") if args else ""
    return str(first).replace("\n", " ")[:80]
