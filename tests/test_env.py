"""Where the agent's commands run: output and file-size caps, time limits, and what LocalEnv
withholds. The Docker tests run where Docker is available, as on GitHub's Linux runners."""

import subprocess
from pathlib import Path

import pytest

from repo_bug_hunter import env
from repo_bug_hunter.env import DockerEnv, docker_available

needs_docker = pytest.mark.skipif(not docker_available(), reason="needs a running Docker")
IMAGE = "ubuntu:24.04"  # small, with bash and coreutils like the SWE-bench images


def test_output_is_stdout_then_stderr_with_the_exit_code(repo):
    assert repo.run("echo out; echo err >&2; exit 3") == (3, "out\nerr\n")


def test_a_flood_of_output_is_cut_and_ended_by_the_timeout(repo, monkeypatch):
    monkeypatch.setattr(env, "MAX_OUTPUT", 1000)
    assert repo.run("yes", timeout=2) == (124, "y\n" * 500 + "\n[output cut at 1,000 bytes]")


def test_a_timeout_keeps_the_output_so_far(repo):
    assert repo.run("echo started; sleep 5", timeout=1) == (124, "started\n")


def test_files_over_the_limit_are_refused(repo, monkeypatch):
    monkeypatch.setattr(env, "MAX_FILE", 10)
    Path(repo.workdir, "big.txt").write_text("x" * 11)
    with pytest.raises(OSError, match="larger than 10 bytes"):
        repo.read(f"{repo.workdir}/big.txt")
    Path(repo.workdir, "small.txt").write_text("x" * 10)
    assert repo.read(f"{repo.workdir}/small.txt") == b"x" * 10


def test_local_commands_do_not_see_credentials(repo, monkeypatch):
    for name in ("SSH_AUTH_SOCK", "DATABASE_URL", "PGPASSWORD", "HTTP_AUTHORIZATION"):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "kept")
    code, out = repo.run("env")
    assert code == 0 and "secret-value" not in out and "GIT_AUTHOR_NAME=kept" in out


def test_no_docker_installed_means_not_available(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert docker_available() is False


@needs_docker
def test_docker_caps_output_and_processes(monkeypatch):
    monkeypatch.setattr(env, "MAX_OUTPUT", 1000)
    with DockerEnv(IMAGE, platform=None) as box:
        assert box.run("echo out; echo err >&2; exit 3") == (3, "out\nerr\n")
        assert box.run("yes", timeout=2) == (124, "y\n" * 500 + "\n[output cut at 1,000 bytes]")
        limit = subprocess.run(["docker", "inspect", "-f", "{{.HostConfig.PidsLimit}}", box.name],
                               capture_output=True, text=True).stdout.strip()
        assert limit == str(env.PIDS_LIMIT)


@needs_docker
def test_docker_file_access_is_limited(monkeypatch):
    monkeypatch.setattr(env, "MAX_FILE", 1000)
    monkeypatch.setattr(env, "FILE_TIMEOUT", 2)
    with DockerEnv(IMAGE, platform=None) as box:
        box.write("/testbed/a/b.txt", b"hello")
        assert box.read("/testbed/a/b.txt") == b"hello"
        with pytest.raises(OSError, match="larger than"):
            box.read("/dev/zero")
        box.run("mkfifo /testbed/pipe")
        with pytest.raises(TimeoutError):  # a named pipe nobody writes to
            box.read("/testbed/pipe")
        with pytest.raises(FileNotFoundError):
            box.read("/testbed/missing")
