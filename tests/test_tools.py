from repo_bug_hunter.tools import Toolbox, is_test_path


def test_read_file_numbers_lines_and_ranges(repo):
    tools = Toolbox(repo)
    out, err = tools.call("read_file", {"path": "calc.py"})
    assert not err
    assert "     2\t    return a - b" in out
    out, _ = tools.call("read_file", {"path": "calc.py", "start_line": 5, "end_line": 6})
    assert out.startswith("[lines 5-6 of 6]")
    assert "return a - b" not in out


def test_read_missing_file_is_an_error(repo):
    out, err = Toolbox(repo).call("read_file", {"path": "nope.py"})
    assert err


def test_search(repo):
    tools = Toolbox(repo)
    out, _ = tools.call("search", {"pattern": r"def (add|mul)"})
    assert "calc.py:1:def add(a, b):" in out and "calc.py:5:def mul(a, b):" in out
    assert tools.call("search", {"pattern": "zzz_nothing"})[0] == "No matches."
    out, err = tools.call("search", {"pattern": "def", "path": ".*"})
    assert err and out.startswith("grep: ")


def test_edit_file(repo):
    tools = Toolbox(repo)
    out, err = tools.call("edit_file", {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"})
    assert not err and "return a + b" in out
    assert "a + b" in (repo.read(f"{repo.workdir}/calc.py").decode())


def test_edit_file_rejects_missing_duplicate_and_empty(repo):
    tools = Toolbox(repo)
    assert "not found" in tools.call("edit_file", {"path": "calc.py", "old_str": "xyz", "new_str": "q"})[0]
    assert "found 2 times" in tools.call("edit_file", {"path": "calc.py", "old_str": "(a, b)", "new_str": "q"})[0]
    assert tools.call("edit_file", {"path": "calc.py", "old_str": "", "new_str": "q"})[1]


def test_write_file_creates_directories(repo):
    tools = Toolbox(repo)
    out, err = tools.call("write_file", {"path": "pkg/new.py", "content": "x = 1\ny = 2\n"})
    assert not err and "2 lines" in out
    assert repo.run("cat pkg/new.py")[1] == "x = 1\ny = 2\n"


def test_bash_reports_exit_code_and_timeout(repo):
    tools = Toolbox(repo)
    out, err = tools.call("bash", {"command": "echo hi; exit 3"})
    assert out == "exit code: 3\nhi\n" and not err
    out, err = tools.call("bash", {"command": "sleep 5", "timeout": 1})
    assert err and "timed out after 1s" in out


def test_long_output_is_truncated(repo):
    out, _ = Toolbox(repo).call("bash", {"command": "python3 -c \"print('x' * 50000)\""})
    assert "characters omitted" in out and len(out) < 11_000


def test_argument_validation(repo):
    tools = Toolbox(repo)
    assert tools.call("fly", {}) == ("Unknown tool: fly", True)
    assert tools.call("read_file", {}) == ("Missing required argument: path", True)
    assert tools.call("read_file", {"path": 3}) == ("Argument path must be a string", True)
    assert tools.call("bash", {"command": "ls", "timeout": "5"})[1]
    assert tools.call("bash", {"command": "ls", "shell": "zsh"}) == ("Unknown argument: shell", True)
    # record_failing_test only exists in test-first mode
    assert tools.call("record_failing_test", {"command": "true"})[1]


def test_patch_has_source_changes_and_new_files_but_not_tests(repo):
    tools = Toolbox(repo)
    tools.call("edit_file", {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"})
    tools.call("write_file", {"path": "helpers.py", "content": "HELP = 1\n"})
    tools.call("write_file", {"path": "repro_test.py", "content": "assert False\n"})
    tools.call("write_file", {"path": "tests/test_extra.py", "content": "def test_x(): pass\n"})
    patch = tools.patch()
    assert "+    return a + b" in patch
    assert "b/helpers.py" in patch
    assert "repro_test.py" not in patch and "test_extra.py" not in patch
    # extracting the patch leaves the index as it was
    assert repo.run("git diff --cached --name-only")[1] == ""


def test_patch_leaves_out_build_artifacts(repo):
    tools = Toolbox(repo)
    tools.call("edit_file", {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"})
    repo.run("python3 -c 'import calc' && printf '\\x89PNG\\x00' > plot.png")  # no .gitignore here
    patch = tools.patch()
    assert "b/calc.py" in patch
    assert "__pycache__" not in patch and "plot.png" not in patch


def test_patch_ignores_changes_that_existed_before_the_agent_started(repo):
    repo.run("echo '# local tweak' >> calc.py && echo junk > setup_artifact.txt")
    tools = Toolbox(repo)
    assert tools.patch() == ""
    tools.call("edit_file", {"path": "calc.py", "old_str": "a * b", "new_str": "b * a"})
    patch = tools.patch()
    assert "+    return b * a" in patch
    assert "+# local tweak" not in patch and "setup_artifact" not in patch


def test_is_test_path():
    assert is_test_path("tests/test_calc.py")
    assert is_test_path("sympy/core/tests/test_basic.py")
    assert is_test_path("repro_test.py") and is_test_path("reproduce.py")
    assert is_test_path("tests/runtests.py")
    assert not is_test_path("django/test/utils.py")  # library code, not a test
    assert not is_test_path("calc.py")


# ---- test-first gate --------------------------------------------------------------

def test_source_is_locked_until_a_failing_test_is_recorded(repo):
    tools = Toolbox(repo, test_first=True)
    fix = {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"}
    out, err = tools.call("edit_file", fix)
    assert err and "locked" in out
    # test files are writable before recording
    assert not tools.call("write_file", {"path": "repro_test.py",
                                         "content": "from calc import add\nassert add(2, 2) == 4\n"})[1]
    out, err = tools.call("record_failing_test", {"command": "python3 repro_test.py"})
    assert not err and "Recorded" in out and tools.repro_status == "reproduced"
    assert not tools.call("edit_file", fix)[1]


def test_a_passing_command_is_not_a_reproduction(repo):
    tools = Toolbox(repo, test_first=True)
    out, err = tools.call("record_failing_test", {"command": "true"})
    assert err and "does not reproduce" in out and tools.repro_command is None


def test_a_command_that_cannot_run_is_not_a_reproduction(repo):
    tools = Toolbox(repo, test_first=True)
    out, err = tools.call("record_failing_test", {"command": "no_such_program repro_test.py"})
    assert err and "could not run" in out and tools.repro_status == "none"


def test_recording_requires_unmodified_source(repo):
    tools = Toolbox(repo, test_first=True)
    repo.run("sed -i.bak 's/a - b/a + b/' calc.py && rm calc.py.bak")  # sneak an edit in via bash
    out, err = tools.call("record_failing_test", {"command": "false"})
    assert err and "calc.py" in out


def test_edits_unlock_after_repeated_failed_attempts(repo):
    tools = Toolbox(repo, test_first=True)
    for _ in range(3):
        tools.call("record_failing_test", {"command": "true"})
    assert tools.repro_status == "unreproduced"
    assert not tools.call("edit_file", {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"})[1]


def test_submit_reruns_the_reproduction(repo):
    tools = Toolbox(repo, test_first=True)
    tools.call("write_file", {"path": "repro_test.py", "content": "from calc import add\nassert add(2, 2) == 4\n"})
    tools.call("record_failing_test", {"command": "python3 repro_test.py"})
    out, err = tools.call("submit", {})
    assert err and "still fails" in out and not tools.submitted
    tools.call("edit_file", {"path": "calc.py", "old_str": "a - b", "new_str": "a + b"})
    assert tools.call("submit", {}) == ("Submitted.", False)
    assert tools.repro_passed_at_submit is True


def test_second_submit_is_accepted_even_if_the_test_still_fails(repo):
    tools = Toolbox(repo, test_first=True)
    tools.call("record_failing_test", {"command": "false"})
    assert tools.call("submit", {})[1]
    assert tools.call("submit", {}) == ("Submitted.", False)
    assert tools.repro_passed_at_submit is False


def test_null_arguments_count_as_left_out(repo):
    tools = Toolbox(repo)  # OpenAI-compatible models often send null for an argument they skip
    out, err = tools.call("read_file", {"path": "calc.py", "start_line": None, "end_line": None})
    assert not err and "return a - b" in out
    assert tools.call("read_file", {"path": None}) == ("Missing required argument: path", True)
