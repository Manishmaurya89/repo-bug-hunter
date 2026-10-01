import pytest

from repo_bug_hunter import agent
from repo_bug_hunter.agent import cost_of, run_agent

from .conftest import FakeQuery, response, text, thinking, tool_use

TASK = {"instance_id": "toy__calc-1", "repo": "toy/calc",
        "problem_statement": "add(2, 2) returns 0 instead of 4."}
FIX = {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"}


def test_fixes_the_bug_and_submits(repo):
    r1 = response(thinking("Look at calc first."), tool_use("read_file", id="a", path="calc.py"))
    r2 = response(text("add subtracts; fixing."), tool_use("edit_file", id="b", **FIX))
    r3 = response(tool_use("bash", id="c", command="python3 -m pytest -q tests"), tool_use("submit", id="d"))
    query = FakeQuery(r1, r2, r3)
    traj = run_agent(TASK, repo, query, model="claude-opus-5-5")
    assert traj["exit_status"] == "submitted"
    assert traj["n_steps"] == 3 and traj["n_tool_calls"] == 4
    assert "+    return a + b" in traj["patch"]
    assert traj["steps"][0]["thinking"] == ["Look at calc first."]
    assert traj["steps"][1]["text"] == ["add subtracts; fixing."]
    assert traj["cost"] == pytest.approx(3 * (1000 * 4 + 100 * 20) / 1e6)
    assert traj["fallback_used"] is False

    # Each request replays earlier assistant turns unchanged (thinking blocks included),
    # each followed by one message holding that turn's tool results.
    third = query.sent[2]
    assert third[0]["content"].startswith("<issue>\nadd(2, 2)")
    assert third[1]["content"] is r1.content and third[3]["content"] is r2.content
    results = third[4]["content"]
    assert [r["tool_use_id"] for r in results] == ["b"] and results[0]["is_error"] is False


def test_all_results_of_a_turn_go_in_one_message(repo):
    query = FakeQuery(
        response(tool_use("read_file", id="a", path="calc.py"), tool_use("search", id="b", pattern="def")),
        response(tool_use("submit", id="c")),
    )
    run_agent(TASK, repo, query, model="claude-opus-5-5")
    results = query.sent[1][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_nudges_when_no_tool_is_called(repo):
    query = FakeQuery(response(text("I think it's fixed."), stop_reason="end_turn"),
                      response(tool_use("submit")))
    traj = run_agent(TASK, repo, query, model="claude-opus-5-5")
    assert "No tool was called" in query.sent[1][2]["content"]
    assert traj["exit_status"] == "submitted" and traj["n_steps"] == 2


def test_step_limit(repo):
    traj = run_agent(TASK, repo, FakeQuery(response(tool_use("bash", command="ls"))),
                     model="claude-opus-5-5", max_steps=3)
    assert traj["exit_status"] == "step_limit" and traj["n_steps"] == 3 and traj["patch"] == ""


def test_cost_limit(repo):
    expensive = response(tool_use("bash", command="ls"), input_tokens=200_000)  # $0.80 a step
    traj = run_agent(TASK, repo, FakeQuery(expensive), model="claude-opus-5-5", max_cost=2.0)
    assert traj["exit_status"] == "cost_limit" and traj["n_steps"] == 3


def test_refusal_stops_the_run(repo):
    traj = run_agent(TASK, repo, FakeQuery(response(stop_reason="refusal")), model="claude-opus-5-5")
    assert traj["exit_status"] == "refusal" and traj["n_steps"] == 1


def test_tool_calls_cut_off_by_max_tokens_are_not_run(repo):
    query = FakeQuery(response(tool_use("edit_file", **FIX), stop_reason="max_tokens"),
                      response(tool_use("submit")))
    traj = run_agent(TASK, repo, query, model="claude-opus-5-5")
    assert traj["steps"][0]["actions"][0]["is_error"]
    assert traj["patch"] == ""


def test_test_first_run_records_reproduction(repo):
    query = FakeQuery(
        response(tool_use("edit_file", **FIX)),  # blocked: nothing recorded yet
        response(tool_use("write_file", path="repro_test.py",
                          content="from calc import add\nassert add(2, 2) == 4\n")),
        response(tool_use("record_failing_test", command="python3 repro_test.py")),
        response(tool_use("edit_file", **FIX)),
        response(tool_use("submit")),
    )
    traj = run_agent(TASK, repo, query, model="claude-opus-5-5", test_first=True)
    assert traj["variant"] == "test_first" and traj["exit_status"] == "submitted"
    assert traj["steps"][0]["actions"][0]["is_error"]
    assert traj["repro"] == {"status": "reproduced", "command": "python3 repro_test.py",
                             "attempts": 0, "passed_at_submit": True}
    assert "+    return a + b" in traj["patch"] and "repro_test" not in traj["patch"]


def test_fallback_turns_are_priced_by_the_serving_model(repo):
    query = FakeQuery(response(tool_use("submit"), model="claude-opus-4-8"))
    traj = run_agent(TASK, repo, query, model="claude-opus-5-5")
    assert traj["fallback_used"] is True
    assert traj["cost"] == pytest.approx((1000 * 5 + 100 * 25) / 1e6)


def test_cost_includes_cache_reads_and_writes():
    usage = response(input_tokens=100, output_tokens=10, cache_read=10_000, cache_write=2_000).usage
    assert cost_of("claude-opus-5-5", usage) == pytest.approx((100 * 4 + 10 * 20 + 2000 * 5 + 10_000 * 0.2) / 1e6)


class _FakeStream:
    def __init__(self, kwargs, log):
        log.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def get_final_message(self):
        return "message"


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-haiku-4-5"])
def test_request_parameters(monkeypatch, model):
    calls = []

    class FakeClient:
        def __init__(self, **kw):
            self.beta = type("B", (), {"messages": type("M", (), {
                "stream": staticmethod(lambda **kw: _FakeStream(kw, calls))})()})()

    monkeypatch.setattr(agent.anthropic, "Anthropic", FakeClient)
    query = agent.claude(model, "medium", "system", [{"name": "t"}])
    assert query([{"role": "user", "content": "hi"}]) == "message"
    sent = calls[0]
    assert sent["cache_control"] == {"type": "ephemeral"} and sent["max_tokens"] == 64_000
    if model == "claude-opus-5-5":
        assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert sent["output_config"] == {"effort": "medium"}
        assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    else:
        assert "thinking" not in sent and "output_config" not in sent and "fallbacks" not in sent


def test_system_prompt_names_the_real_workdir():
    assert "checked out at /testbed " in agent.system_prompt(False)
    p = agent.system_prompt(True, "/tmp/x/repo")
    assert "checked out at /tmp/x/repo " in p and "/tmp/x/repo/repro_test.py" in p and "/testbed" not in p


def test_a_reported_cost_wins_over_the_price_table():
    usage = response(input_tokens=1_000_000).usage
    usage.cost_usd = 0.0123
    assert cost_of("claude-opus-5-5", usage) == 0.0123
