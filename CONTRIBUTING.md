# Contributing

Bug reports, fixes and new experiments are all welcome.

## Set up

```bash
git clone https://github.com/Manishmaurya89/repo-bug-hunter && cd repo-bug-hunter
uv sync
uv run pytest
uvx ruff check
```

The tests need no API key, no network and no Docker: they use a scripted model and a local sandbox. Two tests start real Docker containers and are skipped when Docker isn't running.

## Making a change

- Keep the agent small and readable. New behavior comes with a test.
- Run `uv run pytest` and `uvx ruff check` before you push. CI runs both on Linux and macOS, with Python 3.10 to 3.13.
- Keep each pull request to one topic, and say what changed and how you checked it.
- If you change how the agent behaves, show the effect: run both versions on the same tasks and compare them with `repo-bug-hunter analyze`.

[IMPLEMENTATION.md](IMPLEMENTATION.md) explains how each part works, and [LEARNING_GUIDE.md](LEARNING_GUIDE.md) explains the ideas from zero.

## Releasing

1. Bump `version` in `pyproject.toml`, add an entry to [CHANGELOG.md](CHANGELOG.md), and run `uv lock`.
2. Commit, push to `main`, and wait for the tests to pass.
3. Tag the commit and push the tag:
   ```bash
   git tag -a v0.4.0 -m "repo-bug-hunter 0.4.0" && git push origin v0.4.0
   ```
   The publish workflow builds the package, uploads it to PyPI with trusted publishing and creates the GitHub release.
