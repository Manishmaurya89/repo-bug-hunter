# repo-bug-hunter: measure a coding agent on real GitHub bugs, for free

A small, readable coding agent, and a lab for measuring it. The agent reads a GitHub issue, explores the repository, edits code, runs tests and submits a fix. The lab grades every fix with the project's own hidden tests ([SWE-bench Verified](https://www.swebench.com/)). It tells you, with paired statistics, whether a change to the agent really helped. Every run can be replayed step by step on a static web page.

It costs nothing to run. The default model is a free [OpenRouter](https://openrouter.ai) model, and the whole experiment can run on GitHub's free machines, so you don't need Docker or anything else installed.

## Why this exists, when Copilot can fix bugs

Use GitHub Copilot, Claude Code or similar tools to fix your bugs. repo-bug-hunter answers a different question: **does a bug-fixing agent actually work, and did my change make it better?**

| | A coding assistant | repo-bug-hunter |
|---|---|---|
| Job | Fixes a bug in your repository | Measures how well an agent fixes bugs, and tests ideas |
| Inside | Closed | Every prompt, tool call and output is visible, in about 1,800 lines of Python |
| Change it | No | Change the prompt, tools or rules and measure the effect |
| Models | The vendor's list | Any: free OpenRouter models, Claude, local models in Ollama |
| Evidence | You judge each result yourself | Hidden tests grade every fix, with paired statistics, failure categories and replays |

The idea it tests: **making the agent write a failing test before it may fix anything**, with before-and-after numbers on the same tasks.

## Results

> **Not run yet.** Fill this section in from `results.md`. The numbers below are placeholders that show the shape of the report.

| | baseline | test-first | Change |
|---|---|---|---|
| Resolved (n = _N_ tasks) | _x_% | _y_% | _±z_ pts (95% CI …), McNemar p = … |
| Mean cost per issue | $… | $… | |
| Mean steps per issue | … | … | |

Model: _which model, and settings_. **Replays:** _link to your GitHub Pages site_.

## Quick start

Pick one of three ways to run it:

| | You need | Good for |
|---|---|---|
| [GitHub Actions](#1-github-actions-nothing-to-install) | A GitHub account and a free OpenRouter key | Running experiments with nothing installed |
| [Codespaces](#2-codespaces-in-the-browser) | The same | Trying it in the browser |
| [Your machine](#3-your-machine) | Python 3.10+, [uv](https://docs.astral.sh/uv/), Docker | Local models, development |

Every way starts with a free OpenRouter key from [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys). Free models need no credits.

### 1. GitHub Actions: nothing to install

1. Fork this repository. In your fork's **Actions** tab, enable workflows.
2. Add your key: **Settings → Secrets and variables → Actions → New repository secret**, named `OPENROUTER_API_KEY`.
3. For the replay site: **Settings → Pages → Source: GitHub Actions**.
4. **Actions → experiment → Run workflow.** The defaults run 5 easy tasks with the free model.

Each task runs in its own job on a fresh x86-64 machine with Docker. The agent runs first, then the official SWE-bench grader. When all tasks are done, you get:

- The report, on the workflow run's summary page.
- All results, on the `repo-bug-hunter-results` branch (`runs/<name>/`).
- The replay site, at `https://<you>.github.io/<repo>/`.

To compare the two variants:
1. Run the workflow with name `baseline` and variant `baseline`.
2. Run it again with name `test_first`, variant `test_first` and "compare with" set to `baseline`.

Keep `n`, `seed` and the model the same. If the free models' daily request limit stops a run, run the workflow again the next day with the same name: finished tasks are kept.

GitHub's rules allow Actions for building and testing the repository's own project, and running this project's evaluation is that. Don't use it as the backend of a website.

### 2. Codespaces: in the browser

1. **Code → Codespaces → Create codespace.** It opens with Docker and uv ready.
2. Add `OPENROUTER_API_KEY` as a Codespaces secret (**Settings → Codespaces**), or `export` it in the terminal.
3. Run:

```bash
uv run repo-bug-hunter doctor         # checks Docker, disk, the model and your key
uv run repo-bug-hunter smoke          # a toy bug, end to end, in a minute or two
uv run repo-bug-hunter run --name pilot --n 2 --difficulty "<15 min fix" --workers 1
uv run repo-bug-hunter evaluate runs/pilot && uv run repo-bug-hunter analyze runs/pilot
```

A free GitHub account includes 60 hours a month on a 2-core codespace. Delete the codespace when you're done, because stored codespaces use up the free storage.

### 3. Your machine

You need Python 3.10+, [uv](https://docs.astral.sh/uv/) and Docker (Docker Desktop, [Colima](https://github.com/abiosoft/colima) or [OrbStack](https://orbstack.dev)).
- **CPU:** SWE-bench images are x86-64. On Apple Silicon, turn on Docker Desktop's "Use Rosetta for x86_64/amd64 emulation".
- **Disk:** plan for about 30 GB for a pilot, because each task's image is a few GB. All 500 tasks need about 120 GB.

```bash
git clone https://github.com/Manishmaurya89/repo-bug-hunter && cd repo-bug-hunter
uv sync
export OPENROUTER_API_KEY=...            # free: https://openrouter.ai/settings/keys
uv run repo-bug-hunter doctor
uv run repo-bug-hunter smoke             # runs in a throwaway container; --local if you have no Docker
uv run repo-bug-hunter run --name pilot --n 5 --difficulty "<15 min fix"
uv run repo-bug-hunter evaluate runs/pilot
uv run repo-bug-hunter analyze runs/pilot
```

The experiment uses the same tasks, the same seed and one variable changed:

```bash
uv run repo-bug-hunter run --name baseline   --variant baseline   --n 50
uv run repo-bug-hunter run --name test_first --variant test_first --n 50
uv run repo-bug-hunter evaluate runs/baseline && uv run repo-bug-hunter evaluate runs/test_first
uv run repo-bug-hunter analyze runs/baseline runs/test_first --out results.md
uv run repo-bug-hunter viewer runs/baseline runs/test_first --out site
python -m http.server -d site 8000       # preview at http://localhost:8000
```

To use the command without cloning, run `uv tool install git+https://github.com/Manishmaurya89/repo-bug-hunter` and then `repo-bug-hunter doctor`. Results are written to the current directory.

## Test on fresh bugs from GitHub, outside SWE-bench

SWE-bench's bugs are years old, so a model may have seen their fixes. `repo-bug-hunter pr` builds a task from any merged pull request that fixes an issue, in the same way SWE-bench builds its tasks:

1. The agent gets the issue's text and the repository as it was just before the fix.
2. Its patch is graded with the tests the maintainers wrote for the fix:
   - Tests that failed before the fix must now pass.
   - Tests that passed before must still pass.

```bash
uv run repo-bug-hunter pr more-itertools/more-itertools#1305 python-humanize/humanize#334 --name fresh
uv run repo-bug-hunter analyze runs/fresh
```

- **Supported projects:** Python projects tested with pytest. Their test dependencies are detected from `pyproject.toml` (a test extra or dependency group, or requirements files); override that with `--install`.
- **The issue text is required.** Pull requests that don't name their issue are refused, because their own description would give the fix away. Pass the issue as `owner/repo#PR:ISSUE`.
- **GitHub API limit:** each pull request needs two or three API calls, and GitHub allows 60 an hour without a token. Set `GITHUB_TOKEN` for more.

## Models

| Model | How | Cost |
|---|---|---|
| **Free OpenRouter models** (the default, `poolside/laguna-s-2.1:free`) | `OPENROUTER_API_KEY` | $0. 50 requests a day, or 1,000 a day after buying $10 of credits once; 20 a minute. |
| Paid OpenRouter models | `--model vendor/model` | Billed by OpenRouter. `--max-cost` works, because OpenRouter reports each request's cost. |
| Claude | `ANTHROPIC_API_KEY`, `--model claude-opus-5-5` (or `claude-sonnet-5-5`, `claude-haiku-4-5`) | List prices in [agent.py](repo_bug_hunter/agent.py). Uses prompt caching and adaptive thinking. |
| A local model in [Ollama](https://ollama.com) | `--provider ollama --model <name>` | Free and offline. |
| Any other OpenAI-compatible server (LM Studio, vLLM, …) | `--base-url URL --model NAME`, key in `LLM_API_KEY` | |

- **Daily limit.** A task takes roughly 10–30 requests. Without credits, the free tier runs about 2 tasks a day; with the $10 of credits, about 50. When the daily limit runs out, the run stops cleanly. Run the same command after it resets: finished tasks are kept, and the interrupted one starts over. Shorter rate limits, per minute or at a busy provider, are waited out automatically.
- **Free models come and go.** `repo-bug-hunter doctor` lists the free models that support tool calling today. Keep one model for all the runs you compare.
- **Privacy.** Some free providers may log prompts. SWE-bench code is public, but don't point the agent at private code with a free model.
- **Local models** need tool calling (`ollama show <model>` must list `tools`) and a larger window than Ollama's default of 4,096 tokens. Create a variant with `printf 'FROM gemma4\nPARAMETER num_ctx 32768\n' > Modelfile && ollama create gemma4-32k -f Modelfile`, and use `--workers 1`. Small local models resolve far fewer tasks, so `--difficulty "<15 min fix"` helps.

## How it works

**The agent** ([repo_bug_hunter/agent.py](repo_bug_hunter/agent.py)) is a plain loop around a chat model. It asks for tool calls, runs them and sends back the results, until the agent calls `submit` or hits a step or cost limit. The model comes from [repo_bug_hunter/providers.py](repo_bug_hunter/providers.py), either Claude's own API or any OpenAI-compatible server through [repo_bug_hunter/openai_compat.py](repo_bug_hunter/openai_compat.py). The agent has six tools ([repo_bug_hunter/tools.py](repo_bug_hunter/tools.py)):

| Tool | What it does |
|---|---|
| `read_file` | Read a file with line numbers, 400 lines at a time |
| `search` | `grep -E` across the repository |
| `edit_file` | Replace one exact, unique snippet |
| `write_file` | Create or overwrite a file |
| `bash` | Run any command, such as the project's tests; output is capped at 10,000 characters |
| `submit` | Finish |

**The environment** ([repo_bug_hunter/env.py](repo_bug_hunter/env.py)) is the same Docker image the SWE-bench grader uses for that task, so "the tests passed for the agent" means the same thing as "the tests passed for the grader". Containers have no network, so the agent can't install packages or look up the upstream fix. API keys never enter the container.

**Grading** ([repo_bug_hunter/evaluate.py](repo_bug_hunter/evaluate.py)) uses the official SWE-bench harness. A task counts as resolved only if both hold:
- The hidden tests written for the real fix now pass (`FAIL_TO_PASS`).
- The tests that passed before still pass (`PASS_TO_PASS`).

### The experiment: test-first, enforced by the harness

The baseline agent is never told to reproduce the bug. The `test_first` variant adds one tool, `record_failing_test`, and a rule that the harness enforces rather than merely suggests:

1. **Red.** Source files are locked. The agent has to write a test and register the command that runs it. The harness runs that command against the unmodified code and records it only if it fails.
2. **Green.** When the agent calls `submit`, the harness runs the command again. If it still fails, the first submit is rejected and the agent keeps working.
3. **Escape hatches.** After three commands that don't fail, the source files unlock anyway, and the second submit is always accepted. Both cases are recorded, so the analysis can ask whether a real reproduction helped.

The only difference between the two runs is the gate: they use the same tasks, model, limits and base prompt.

### Why tasks fail

Every unresolved task is put in exactly one category. The first rule that matches wins ([repo_bug_hunter/analyze.py](repo_bug_hunter/analyze.py)):

| Category | Rule |
|---|---|
| Gave up | Hit the step or cost limit or refused without submitting, or submitted an empty patch |
| Patch did not apply | The grader could not apply the patch |
| Grader error | The grader produced no test results, for example because the tests timed out |
| Wrong file | The patch touches none of the files the real fix changed |
| Broke other tests | Some previously passing tests now fail |
| Right file, tests still fail | Right place, nothing else broken, but the issue isn't fixed |

Runs stopped by Docker, the API or a daily request limit are not the agent's result. They're left out of the numbers, listed as "not finished", and redone from the start when you run the same command again. A half-done patch is never scored as a success or a failure.

### Statistics

With 50–100 tasks, chance alone can move the resolve rate by several points:

- Each resolve rate comes with a 95% Wilson confidence interval.
- The two variants are compared **paired**, on the same tasks. McNemar's exact test uses only the tasks where exactly one variant succeeded. A paired bootstrap gives a 95% interval for the difference.
- The report says plainly when a difference could be noise. Model output also varies from run to run, so the strongest evidence is repeating each variant and showing that the gap holds.

## Honest notes

- **SWE-bench Verified is contaminated for frontier models.** In February 2026, OpenAI stopped reporting it, after finding flawed tests and models that could reproduce the real fixes from memory. Absolute scores here may be inflated the same way. The paired before/after comparison is less affected, because both variants use the same model and so remember the same things. The public set of [SWE-bench Pro](https://github.com/scaleapi/SWE-bench_Pro-os) would be the next step.
- **Top agents score much higher.** This project shows how to measure an agent and improve it with evidence, not how to beat frontier agents. [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) is a good minimal reference.
- **Some rules are heuristics.** Files named `test_*`, `*_test.py`, `repro*`, or under a `tests/` directory count as tests: they stay out of the patch and are never locked. "Wrong file" compares against the files the real fix changed, so a correct fix in a different file would be miscounted.
- **The lock covers the edit tools.** An agent could still change source files through `bash`, but it can't record a reproduction while source files are modified.
- **Small context windows.** With a small local model, the oldest tool outputs are removed from the prompt as it nears the window, six at a time. The agent is told it can re-run a command to see an output again.
- **Tool calling is the weak spot of small models.** Some write a tool call out as text instead of making one. The agent is told when a turn had no tool call, and these turns show up in the replays.
- **Security.** The agent runs commands a model wrote. SWE-bench tasks run in the task's container with no network. The smoke test also runs in a container by default. With `--local`, it runs on your machine instead, in a temporary directory, with environment variables that look like credentials removed.
- **Costs** for Claude come from list prices in `PRICES` in [repo_bug_hunter/agent.py](repo_bug_hunter/agent.py) (September 2026); OpenRouter reports its own. Other models show $0, and the report shows tokens per issue alongside.

## Files

```
repo_bug_hunter/cli.py            the `repo-bug-hunter` command
repo_bug_hunter/doctor.py         checks Docker, disk, the model and its key
repo_bug_hunter/providers.py      which model: OpenRouter (default), Claude, Ollama or any OpenAI-compatible server
repo_bug_hunter/agent.py          the loop, prompts, pricing, Claude request settings
repo_bug_hunter/openai_compat.py  OpenAI-compatible models; rate limits and daily limits
repo_bug_hunter/tools.py          the tools, the test-first gate, patch extraction
repo_bug_hunter/env.py            the Docker environment (and a local one for tests)
repo_bug_hunter/tasks.py          loading SWE-bench Verified, seeded task selection
repo_bug_hunter/run.py            run the agent over a reproducible sample of tasks
repo_bug_hunter/pr.py             tasks from merged GitHub pull requests, graded with the fix's own tests
repo_bug_hunter/evaluate.py       grade with the official harness
repo_bug_hunter/analyze.py        resolve rate, cost, steps, failure categories, paired comparison
repo_bug_hunter/viewer.py         build the static replay site; repo_bug_hunter/site/index.html is the page
repo_bug_hunter/merge.py          combine results from parallel jobs
repo_bug_hunter/smoke.py          end-to-end check on a toy bug
.github/workflows/                experiment.yml (run on GitHub), tests.yml (CI)
.devcontainer/                    Codespaces setup
runs/<name>/                      config.json, trajs/*.json, preds.jsonl, eval.json
```

[IMPLEMENTATION.md](IMPLEMENTATION.md) explains how each part is built and why. [LEARNING_GUIDE.md](LEARNING_GUIDE.md) explains every idea from zero.

## License

[MIT](LICENSE)
