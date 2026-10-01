"""repo-bug-hunter: measure a coding agent on real GitHub bugs.

    repo-bug-hunter demo        replay example runs in the browser: no API key or Docker needed
    repo-bug-hunter setup       save your model and API key once
    repo-bug-hunter doctor      check Docker, disk, the model and its key
    repo-bug-hunter smoke       fix a toy bug end to end, in minutes
    repo-bug-hunter run         run the agent on SWE-bench Verified tasks
    repo-bug-hunter pr          run it on real bug fixes from GitHub pull requests, outside SWE-bench
    repo-bug-hunter evaluate    grade a run with the official SWE-bench harness
    repo-bug-hunter analyze     resolve rate, cost, steps, failure categories, before/after statistics
    repo-bug-hunter viewer      build the static replay site
    repo-bug-hunter merge       combine results from parallel jobs (used by the GitHub Actions workflow)

New here? Start with `repo-bug-hunter demo`. To run the agent yourself, save an API key with
`repo-bug-hunter setup` (a free OpenRouter key works), then `repo-bug-hunter doctor` checks the rest.
`repo-bug-hunter <command> --help` shows a command's options.
"""

from __future__ import annotations

import importlib
import sys

from . import config

COMMANDS = ("demo", "setup", "doctor", "smoke", "run", "pr", "evaluate", "analyze", "viewer", "merge")
POSIX_ONLY = ("doctor", "smoke", "run", "pr", "evaluate")  # they run bash and Linux tools


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return
    command = sys.argv[1]
    if command not in COMMANDS:
        sys.exit(f"repo-bug-hunter: unknown command {command!r}\n\n{__doc__.strip()}")
    if command in POSIX_ONLY and sys.platform == "win32":
        sys.exit(f"repo-bug-hunter {command} needs macOS or Linux. On Windows, run it inside WSL2 "
                 "(`wsl --install`) or in GitHub Codespaces. `repo-bug-hunter demo` works here too.")
    if command != "setup":  # setup --show tells saved keys from ones in the environment
        config.apply()
    sys.argv = [f"repo-bug-hunter {command}", *sys.argv[2:]]
    importlib.import_module(f"repo_bug_hunter.{command}").main()


if __name__ == "__main__":
    main()
