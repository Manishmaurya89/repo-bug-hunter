"""Where the agent's commands run.

DockerEnv runs inside the same SWE-bench image the grader uses, so "it passed
for the agent" and "it passed for the grader" mean the same environment.
LocalEnv runs in a plain directory; it exists for the unit tests and toy tasks.

Both keep a model's mistakes from taking this machine down with them: command output and file
reads are capped, so a test stuck printing in a loop, or a read of /dev/zero, can't fill memory.
"""

from __future__ import annotations

import os
import re
import selectors
import shlex
import subprocess
import time
import uuid
from pathlib import Path

# Same activation the SWE-bench eval script uses.
CONDA = "source /opt/miniconda3/bin/activate >/dev/null 2>&1; conda activate testbed >/dev/null 2>&1; "
# Variables LocalEnv withholds from commands. AUTH catches SSH_AUTH_SOCK (the SSH agent) but not
# GIT_AUTHOR_*; _URL$ catches DATABASE_URL and other URLs that can carry a password.
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|CREDENTIAL|AUTH(?!OR_)|COOKIE|SESSION|DSN|_URL$", re.I)
MAX_OUTPUT = 10_000_000  # bytes kept of a command's stdout, and of its stderr
MAX_FILE = 10_000_000    # bytes; larger files can't be read or edited through the tools
FILE_TIMEOUT = 60        # seconds for reading or writing a file: a named pipe would block forever
PIDS_LIMIT = 4096        # processes per container, so a fork bomb can't take Docker down


class DockerEnv:
    workdir = "/testbed"

    def __init__(self, image: str, network: bool = False, platform: str | None = "linux/amd64"):
        # SWE-bench images exist only for x86-64; platform=None runs an image natively.
        self.image = image
        self.network = network
        self.platform = platform
        self.name = f"repo-bug-hunter-{uuid.uuid4().hex[:10]}"

    def __enter__(self) -> "DockerEnv":
        cmd = ["docker", "run", "-d", "--rm", "--name", self.name, "-w", self.workdir,
               "--pids-limit", str(PIDS_LIMIT)]
        if self.platform:
            cmd += ["--platform", self.platform]
        if not self.network:
            # No network: the agent can't pip install, and can't look up the upstream fix.
            cmd += ["--network", "none"]
        p = subprocess.run(cmd + [self.image, "sleep", "infinity"], capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"docker run {self.image} failed: {p.stderr.decode(errors='replace').strip()}")
        self.run("git config --global --add safe.directory /testbed", timeout=30)
        return self

    def __exit__(self, *exc) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

    def run(self, command: str, timeout: int = 180) -> tuple[int, str]:
        # `timeout` runs inside the container so a hung test is actually killed,
        # not just abandoned by the docker client.
        argv = ["docker", "exec", "-w", self.workdir, self.name,
                "timeout", "-k", "5", str(timeout), "bash", "-c", CONDA + command]
        return capture(argv, timeout + 30)

    def read(self, path: str) -> bytes:
        p = subprocess.run(["docker", "exec", self.name, "timeout", str(FILE_TIMEOUT),
                            "head", "-c", str(MAX_FILE + 1), "--", path],
                           capture_output=True, timeout=FILE_TIMEOUT + 30)
        if p.returncode == 124:
            raise TimeoutError(f"reading {path} took more than {FILE_TIMEOUT}s")
        if p.returncode != 0:
            raise FileNotFoundError(p.stderr.decode(errors="replace").strip() or path)
        return within_limit(path, p.stdout)

    def write(self, path: str, data: bytes) -> None:
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        p = subprocess.run(["docker", "exec", "-i", self.name, "timeout", str(FILE_TIMEOUT),
                            "sh", "-c", script, "sh", path],
                           input=data, capture_output=True, timeout=FILE_TIMEOUT + 30)
        if p.returncode != 0:
            raise OSError(p.stderr.decode(errors="replace").strip() or f"could not write {path}")


class LocalEnv:
    """Runs commands on the host in `root`. Not sandboxed: use for tests and toy repos only.
    Environment variables that look like credentials are withheld from the commands, since
    their output goes to the model."""

    def __init__(self, root: str | Path):
        self.workdir = str(Path(root).resolve())

    def __enter__(self) -> "LocalEnv":
        return self

    def __exit__(self, *exc) -> None:
        pass

    def run(self, command: str, timeout: int = 180) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if not SECRET_NAME.search(k)}
        return capture(["bash", "-c", command], timeout, cwd=self.workdir, env=env)

    def read(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return within_limit(path, f.read(MAX_FILE + 1))

    def write(self, path: str, data: bytes) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)


def capture(argv: list[str], timeout: float, **popen) -> tuple[int, str]:
    """Run argv and return its exit code and output (stdout, then stderr), like subprocess.run
    with capture_output. Only the first MAX_OUTPUT bytes of each stream are kept: the rest is read
    and dropped, so the command isn't blocked and memory stays bounded. A command still running at
    `timeout` is killed and gets exit code 124, as with `timeout`; its output so far is kept."""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen)
    kept = {p.stdout: bytearray(), p.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    with p, selectors.DefaultSelector() as sel:
        for stream in kept:
            sel.register(stream, selectors.EVENT_READ)
        while sel.get_map() and (left := deadline - time.monotonic()) > 0:
            for key, _ in sel.select(left):
                chunk = os.read(key.fd, 1 << 16)
                if not chunk:
                    sel.unregister(key.fileobj)
                buf = kept[key.fileobj]
                buf += chunk[: MAX_OUTPUT - len(buf)]
        try:
            code = p.wait(timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            code = 124
    out = b"".join(kept.values()).decode("utf-8", errors="replace")
    if any(len(buf) >= MAX_OUTPUT for buf in kept.values()):
        out += f"\n[output cut at {MAX_OUTPUT:,} bytes]"
    return code, out


def within_limit(path: str, data: bytes) -> bytes:
    if len(data) > MAX_FILE:
        raise OSError(f"{path} is larger than {MAX_FILE:,} bytes; look at it with bash (head, grep) instead")
    return data


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):  # not installed, or the daemon doesn't answer
        return False


def quote(*args: str) -> str:
    return " ".join(shlex.quote(a) for a in args)
