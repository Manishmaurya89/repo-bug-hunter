import subprocess
from types import SimpleNamespace

import pytest

from repo_bug_hunter import config
from repo_bug_hunter.env import LocalEnv

CALC = "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"


@pytest.fixture(autouse=True)
def no_saved_settings(tmp_path_factory, monkeypatch):
    """Keep tests away from settings saved on this machine by `repo-bug-hunter setup`."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    for name in config.MODEL_VARS.values():
        monkeypatch.delenv(name, raising=False)
    config.from_file.clear()


@pytest.fixture
def repo(tmp_path):
    """A tiny git repo with a bug: add() subtracts."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text(CALC)
    (root / "tests" / "test_calc.py").write_text(TEST)

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return LocalEnv(root)


# ---- a scripted stand-in for the Claude API ----------------------------------

def tool_use(name, id="t1", **input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def text(s):
    return SimpleNamespace(type="text", text=s)


def thinking(s):
    return SimpleNamespace(type="thinking", thinking=s, signature="sig")


def response(*content, stop_reason="tool_use", model="claude-opus-5-5", input_tokens=1000,
             output_tokens=100, cache_read=0, cache_write=0):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                            cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write)
    return SimpleNamespace(content=list(content), stop_reason=stop_reason, usage=usage, model=model)


class FakeQuery:
    """Returns scripted responses in order; records the messages it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def __call__(self, messages):
        self.sent.append(list(messages))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]  # repeat the last response forever
