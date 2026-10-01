# Changelog

## 0.4.0

- `repo-bug-hunter setup` saves your model and API key once, in a file only you can read, and every command uses them. `setup --show` and `setup --forget` manage what is saved.
- `doctor` says when it is using the saved settings.
- The Docker tests pull their image from AWS's mirror of Docker Hub, so CI no longer fails on Docker Hub's anonymous pull limit.
- CI lints with ruff and also tests on Python 3.13. Dependabot keeps the GitHub Actions and the Python packages up to date.
- Code cleanup: shorter comments, no lambda assignments, `zip(strict=True)` where lengths must match.

## 0.3.0 (2026-10-01)

- `repo-bug-hunter demo` replays example runs in the browser, with no API key or Docker.
- A first-run hint, and a clear message on Windows instead of a crash.
- HTTPS works on Python from python.org on macOS, which has no system certificates by default. `doctor` no longer reports a connection problem as a rejected key.

## 0.2.0 (2026-10-01)

- First release on PyPI: the agent, the test-first experiment, grading with the official SWE-bench harness, analysis, the replay site, and tasks built from GitHub pull requests.
