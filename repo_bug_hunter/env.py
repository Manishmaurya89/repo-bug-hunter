"""Where the agent's commands run.

DockerEnv runs inside the same SWE-bench image the grader uses, so "it passed
for the agent" and "it passed for the grader" mean the same environment.
LocalEnv runs in a plain directory; it exists for the unit tests and toy tasks.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import uuid
from pathlib import Path

# Same activation the SWE-bench eval script uses.
CONDA = "source /opt/miniconda3/bin/activate >/dev/null 2>&1; conda activate testbed >/dev/null 2>&1; "
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)


class DockerEnv:
    workdir = "/testbed"

    def __init__(self, image: str, network: bool = False, platform: str | None = "linux/amd64"):
        # SWE-bench images exist only for x86-64; platform=None runs an image natively.
        self.image = image
        self.network = network
        self.platform = platform
        self.name = f"repo-bug-hunter-{uuid.uuid4().hex[:10]}"

    def __enter__(self) -> "DockerEnv":
        cmd = ["docker", "run", "-d", "--rm", "--name", self.name, "-w", self.workdir]
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
        try:
            p = subprocess.run(argv, capture_output=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            return 124, ""
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")

    def read(self, path: str) -> bytes:
        p = subprocess.run(["docker", "exec", self.name, "cat", "--", path], capture_output=True)
        if p.returncode != 0:
            raise FileNotFoundError(p.stderr.decode(errors="replace").strip() or path)
        return p.stdout

    def write(self, path: str, data: bytes) -> None:
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        p = subprocess.run(["docker", "exec", "-i", self.name, "sh", "-c", script, "sh", path],
                           input=data, capture_output=True)
        if p.returncode != 0:
            raise OSError(p.stderr.decode(errors="replace").strip())


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
        try:
            p = subprocess.run(["bash", "-c", command], cwd=self.workdir, env=env,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, ""
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace")

    def read(self, path: str) -> bytes:
        return Path(path).read_bytes()

    def write(self, path: str, data: bytes) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)


def docker_available() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def quote(*args: str) -> str:
    return " ".join(shlex.quote(a) for a in args)
