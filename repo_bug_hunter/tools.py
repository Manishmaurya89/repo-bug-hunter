"""The agent's tools, and the test-first gate.

Every tool returns (output_text, is_error). Tools never raise on bad input:
the model gets an error message it can act on instead.
"""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath

from .env import quote

MAX_OUTPUT = 10_000   # characters of command output shown to the model
MAX_READ_LINES = 400
MAX_SEARCH_HITS = 100
REPRO_ATTEMPTS = 3    # failed record_failing_test calls before edits unlock anyway
# New files the agent creates go into the patch only if they look like source. This keeps out
# bytecode, plots and other artifacts from test runs, which would otherwise break `git apply`.
SOURCE_SUFFIXES = (".py", ".pyi", ".pyx", ".pxd", ".c", ".h", ".cfg", ".ini", ".toml", ".txt",
                   ".rst", ".md", ".json", ".yml", ".yaml", ".html", ".css", ".js")


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n[... {len(text) - limit} characters omitted ...]\n{text[-half:]}"


def is_test_path(path: str) -> bool:
    """Test files and reproduction scripts: kept out of the patch and outside the edit gate."""
    p = PurePosixPath(path)
    name = p.name
    return (
        name.startswith(("test_", "repro"))
        or name.endswith(("_test.py", "_tests.py"))
        or name in ("tests.py", "conftest.py")
        or "tests" in p.parts[:-1]
    )


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    # No eager_input_streaming: partial deltas aren't used, and server-side validation catches truncated edits.
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties, "required": required}}


STR = {"type": "string"}
INT = {"type": "integer"}

BASE_TOOLS = [
    _tool("bash", "Run a shell command from the repository root and return its exit code and "
          "output (stdout and stderr). Use it to run tests or scripts and to inspect files and git "
          "state. Each call starts a fresh shell, so `cd` does not persist. Long output is cut to "
          "its first and last 5,000 characters.",
          {"command": STR, "timeout": {**INT, "description": "Seconds, default 180, max 900."}},
          ["command"]),
    _tool("read_file", f"Read a file with line numbers, at most {MAX_READ_LINES} lines per call.",
          {"path": STR, "start_line": INT, "end_line": INT}, ["path"]),
    _tool("search", "Search file contents with an extended regular expression (grep -E). "
          f"Returns up to {MAX_SEARCH_HITS} matches as path:line:text.",
          {"pattern": STR, "path": {**STR, "description": "File or directory, default: repository root."}},
          ["pattern"]),
    _tool("edit_file", "Replace exactly one occurrence of old_str with new_str in a file. old_str "
          "must match the file exactly, including indentation, and must be unique in the file; "
          "include surrounding lines if needed.",
          {"path": STR, "old_str": STR, "new_str": STR}, ["path", "old_str", "new_str"]),
    _tool("write_file", "Create a file, or overwrite an entire file, with the given content.",
          {"path": STR, "content": STR}, ["path", "content"]),
    _tool("submit", "Submit the current state of the repository as your fix. This ends the task.",
          {}, []),
]

RECORD_TOOL = _tool(
    "record_failing_test",
    "Register the command that reproduces the issue, e.g. `python repro_test.py`. The harness runs "
    "it now against the unmodified source and it must fail (non-zero exit code). Source files can "
    "be edited only after this succeeds. On submit, the harness runs it again and it must pass.",
    {"command": STR}, ["command"])


def tool_schemas(test_first: bool) -> list[dict]:
    return BASE_TOOLS + ([RECORD_TOOL] if test_first else [])


class Toolbox:
    def __init__(self, env, test_first: bool = False):
        self.env = env
        self.test_first = test_first
        self.submitted = False
        # Test-first bookkeeping, saved into the trajectory.
        self.repro_command: str | None = None
        self.repro_attempts = 0
        self.repro_status = "none"   # -> "reproduced", or "unreproduced" after REPRO_ATTEMPTS misses
        self.repro_passed_at_submit: bool | None = None
        self.submit_rejections = 0
        self._snapshot()

    @property
    def schemas(self) -> list[dict]:
        return tool_schemas(self.test_first)

    # ---- dispatch -------------------------------------------------------

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        handler = getattr(self, f"_{name}", None)
        if handler is None or (name == "record_failing_test" and not self.test_first):
            return f"Unknown tool: {name}", True
        if "_invalid_json" in args:  # set by the OpenAI-compatible adapter
            return f"Your arguments for {name} were not valid JSON: {args['_invalid_json'][:500]}", True
        spec = next(t for t in self.schemas if t["name"] == name)["input_schema"]
        # Some OpenAI-compatible models send null for an optional argument they leave out.
        args = {k: v for k, v in args.items() if v is not None}
        for key in spec["required"]:
            if key not in args:
                return f"Missing required argument: {key}", True
        for key, value in args.items():
            want = spec["properties"].get(key, {}).get("type")
            if want is None:
                return f"Unknown argument: {key}", True
            if (want == "string" and not isinstance(value, str)) or \
               (want == "integer" and (not isinstance(value, int) or isinstance(value, bool))):
                return f"Argument {key} must be a {want}", True
        try:
            return handler(**args)
        except Exception as e:  # environment failures become tool errors, not crashes
            return f"{type(e).__name__}: {e}", True

    def _abs(self, path: str) -> str:
        return posixpath.normpath(posixpath.join(self.env.workdir, path))

    def _in_repo(self, path: str) -> bool:
        return path == self.env.workdir or path.startswith(self.env.workdir.rstrip("/") + "/")

    def _locked(self, path: str) -> bool:
        """Test-first: source files stay read-only until a failing test is recorded."""
        if not self.test_first or self.repro_status != "none":
            return False
        return self._in_repo(path) and not is_test_path(posixpath.relpath(path, self.env.workdir))

    # ---- tools ------------------------------------------------------------

    def _bash(self, command: str, timeout: int = 180) -> tuple[str, bool]:
        timeout = max(1, min(timeout, 900))
        code, out = self.env.run(command, timeout=timeout)
        if code == 124:
            return truncate(out) + f"\n[command timed out after {timeout}s]", True
        return f"exit code: {code}\n{truncate(out)}", False

    def _read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> tuple[str, bool]:
        lines = self.env.read(self._abs(path)).decode("utf-8", errors="replace").splitlines()
        start = max(1, start_line)
        end = min(len(lines), end_line or len(lines), start + MAX_READ_LINES - 1)
        body = "\n".join(f"{i:6}\t{lines[i - 1]}" for i in range(start, end + 1))
        if start > 1 or end < len(lines):
            body = f"[lines {start}-{end} of {len(lines)}]\n{body}"
        return body or "[empty file]", False

    def _search(self, pattern: str, path: str = ".") -> tuple[str, bool]:
        cmd = f"grep -rnIE --exclude-dir=.git -e {quote(pattern)} -- {quote(path)} | head -n {MAX_SEARCH_HITS + 1}"
        code, out = self.env.run(cmd, timeout=60)
        hits = out.splitlines()
        if hits and all(line.startswith("grep: ") for line in hits):
            return "\n".join(hits), True  # e.g. a bad regex or a path that does not exist
        if not hits:
            return "No matches.", False
        more = len(hits) > MAX_SEARCH_HITS
        text = "\n".join(line[:300] for line in hits[:MAX_SEARCH_HITS])
        return text + ("\n[more matches omitted; narrow the pattern or path]" if more else ""), False

    def _edit_file(self, path: str, old_str: str, new_str: str) -> tuple[str, bool]:
        full = self._abs(path)
        if self._locked(full):
            return self._locked_message(), True
        if not old_str:
            return "old_str is empty. Use write_file to create or overwrite a file.", True
        try:
            text = self.env.read(full).decode("utf-8")
        except UnicodeDecodeError:
            return f"{path} is not valid UTF-8; edit it with bash instead.", True
        count = text.count(old_str)
        if count != 1:
            hint = "not found" if count == 0 else f"found {count} times; include more surrounding lines"
            return f"old_str {hint} in {path}. No change made.", True
        new_text = text.replace(old_str, new_str, 1)
        self._write(full, new_text.encode("utf-8"))
        first = text[: text.index(old_str)].count("\n") + 1
        lines = new_text.splitlines()
        lo, hi = max(1, first - 3), min(len(lines), first + new_str.count("\n") + 3)
        snippet = "\n".join(f"{i:6}\t{lines[i - 1]}" for i in range(lo, hi + 1))
        return f"Edited {path}. Lines {lo}-{hi} now read:\n{snippet}", False

    def _write_file(self, path: str, content: str) -> tuple[str, bool]:
        full = self._abs(path)
        if self._locked(full):
            return self._locked_message(), True
        self._write(full, content.encode("utf-8"))
        return f"Wrote {len(content.splitlines())} lines to {path}.", False

    def _record_failing_test(self, command: str) -> tuple[str, bool]:
        dirty = self._modified_source_files()
        if dirty:
            return (f"Source files are already modified: {', '.join(dirty)}. Revert them with "
                    f"`git checkout {self.base} -- {quote(*dirty)}` so the test runs against the "
                    "original code, then try again."), True
        code, out = self.env.run(command, timeout=300)
        tail = truncate(out, 3000)
        if code in (126, 127):  # the shell could not run it at all: that proves nothing about the bug
            return f"Not recorded: the command could not run (exit code {code}).\n{tail}", True
        if code != 0 and code != 124:
            self.repro_command = command
            self.repro_status = "reproduced"
            return (f"Recorded. The command fails on the current code (exit code {code}), as it should. "
                    f"Source files are now editable. On submit it must pass.\n{tail}"), False
        self.repro_attempts += 1
        why = "timed out" if code == 124 else "passed (exit code 0), so it does not reproduce the issue"
        msg = f"Not recorded: the command {why}.\n{tail}"
        if self.repro_command is None and self.repro_attempts >= REPRO_ATTEMPTS:
            self.repro_status = "unreproduced"
            msg += (f"\nAfter {REPRO_ATTEMPTS} attempts, source files are unlocked anyway; "
                    "continue without a recorded reproduction.")
        return msg, True

    def _submit(self) -> tuple[str, bool]:
        if self.test_first and self.repro_command:
            code, out = self.env.run(self.repro_command, timeout=300)
            self.repro_passed_at_submit = code == 0
            if code != 0 and self.submit_rejections == 0:
                self.submit_rejections += 1
                return (f"Not submitted: your reproduction command `{self.repro_command}` still fails "
                        f"(exit code {code}). Fix the code, or the test if the test is wrong, then "
                        f"submit again.\n{truncate(out, 3000)}"), True
        self.submitted = True
        return "Submitted.", False

    def _write(self, path: str, data: bytes) -> None:
        self.env.write(path, data)
        if path.endswith(".py"):
            # Python trusts a cached .pyc whose recorded size and mtime (whole seconds) match the
            # source, so a same-size edit right after a run would silently execute the old code.
            folder, name = posixpath.split(path)
            self.env.run(f"rm -f {quote(folder)}/__pycache__/{quote(name[:-3])}.*.pyc", timeout=30)

    def _locked_message(self) -> str:
        return ("Source files are locked until you register a failing reproduction with "
                f"record_failing_test. You can create test files first, e.g. {self.env.workdir}/repro_test.py.")

    # ---- git ----------------------------------------------------------------

    def _git(self, args: str) -> str:
        return self.env.run(f"git {args} 2>/dev/null", timeout=120)[1]

    def _snapshot(self) -> None:
        """Remember the starting tree, so pre-existing changes in the image stay out of the patch."""
        stash = self._git("-c user.name=repo-bug-hunter -c user.email=repo-bug-hunter@localhost stash create")
        self.base = stash.strip() or "HEAD"
        self.preexisting = set(self._git("ls-files --others --exclude-standard").split("\n"))

    def _new_files(self) -> list[str]:
        out = self._git("ls-files --others --exclude-standard")
        return [f for f in out.split("\n") if f and f not in self.preexisting]

    def _modified_source_files(self) -> list[str]:
        changed = self._git(f"diff --name-only {self.base}").split("\n")
        return sorted(f for f in changed if f and not is_test_path(f))

    def patch(self) -> str:
        """The fix as a unified diff: changes to tracked files plus new non-test source files."""
        new = [f for f in self._new_files() if not is_test_path(f) and f.endswith(SOURCE_SUFFIXES)]
        if new:
            self._git("add -N -- " + quote(*new))
        diff = self._git(f"-c core.fileMode=false diff --binary --no-color {self.base}")
        if new:
            self._git("reset -q -- " + quote(*new))
        return diff
