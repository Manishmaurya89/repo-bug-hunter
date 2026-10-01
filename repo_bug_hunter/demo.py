"""Replay example runs in the browser: no API key and no Docker needed.

    repo-bug-hunter demo                 # opens the replay site in your browser
    repo-bug-hunter demo --no-browser    # only print its address

The examples are real runs on SWE-bench tasks that ship with the package: watch every step the
agent took and whether the hidden tests passed. To replay your own runs, use
`repo-bug-hunter viewer runs/<name>`.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import tempfile
import webbrowser
from pathlib import Path

from .viewer import build

EXAMPLES = ("free_pilot", "local_pilot", "local_pilot_tf", "openrouter_pilot")


def example_runs() -> list[Path]:
    """The example runs: inside the installed package, or in runs/ of a source checkout."""
    here = Path(__file__).resolve().parent
    for root in (here / "examples", here.parent / "runs"):
        runs = [root / name for name in EXAMPLES if (root / name / "eval.json").exists()]
        if runs:
            return runs
    return []


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # a line per request would bury the address
        pass


def serve(site: Path, port: int = 0) -> http.server.ThreadingHTTPServer:
    """A web server for `site`, reachable from this machine only."""
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(QuietHandler, directory=str(site)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="default: any free port")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser")
    args = ap.parse_args()
    runs = example_runs()
    if not runs:
        ap.error("the example runs are missing from this installation")
    with tempfile.TemporaryDirectory(prefix="repo-bug-hunter-demo-") as site:
        build(runs, Path(site))
        with serve(Path(site), args.port) as server:
            url = f"http://127.0.0.1:{server.server_port}/"
            print(f"Replaying {len(runs)} example runs at {url}  (Ctrl+C to stop)", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print()
