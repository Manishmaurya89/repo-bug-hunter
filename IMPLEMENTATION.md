# How repo-bug-hunter was built

This file records what we built, how each part works, and why it was built that way. The [README](README.md) explains how to use the project. This file explains the implementation and the history behind it.

> **New to these terms?** Read [LEARNING_GUIDE.md](LEARNING_GUIDE.md) first. It explains every word used here in simple language, with examples from this project.

**Status on 30 September 2026:** the whole pipeline is built and all 56 tests pass. Both variants have run end to end on one real SWE-bench task using a free local model. The baseline was not resolved and the test-first run was. That's one task, so it's an encouraging example, not proof. The full experiment (50 tasks × 2 variants) has not been run yet, so the README has no Results section yet.

---

## 1. What the project is

repo-bug-hunter is a small coding agent. It reads a GitHub issue, explores the repository, edits the code, runs tests and submits a fix. It is measured on **SWE-bench Verified**, a set of real bugs from popular Python projects. Each fix is checked by the project's own tests.

The project is about **measurement**, not about getting a high score:

- It reports the percentage of issues solved, the cost per issue and the steps per issue.
- It tests one idea with before-and-after numbers: **making the agent write a failing test before it fixes anything** ("test-first").
- It sorts every failure into one category, such as wrong file, broke other tests or gave up.
- Every run can be replayed step by step on a static web page that can be hosted for free.

---

## 2. What we did, step by step

### Step 1: Choosing the project (29 Sep, first session)

We looked for AI/LLM projects that match what 2026 job descriptions for AI engineer roles in India ask for. The same four skills came up again and again: agents, structured output, evaluation and deployment. We also wanted a project a recruiter can see live. From the shortlist we picked **"a coding agent that fixes real GitHub bugs"** because:

- Agents appear in most AI engineer job descriptions.
- SWE-bench gives real, objective grading, so the results are numbers rather than "vibes".
- It answers the interview question "where does your system fail, and how do you know?"
- The replay page is a free live demo.

### Step 2: Building the project (29 Sep, second session)

The folder was empty, so everything was built from scratch in this order:

1. **Setup.** We created a Python project with `uv` and installed the Anthropic SDK, the official `swebench` harness (5.0.2), Hugging Face `datasets` and `pytest`.
2. **Checked the harness.** `swebench` 5.x works differently from older versions: each dataset row now names its own Docker image, eval script and log parser. We read its source code to see how it picks images and where it writes reports, and built the grading step to match.
3. **Environment layer** ([repo_bug_hunter/env.py](repo_bug_hunter/env.py)): runs commands inside the task's Docker container.
4. **Tool layer** ([repo_bug_hunter/tools.py](repo_bug_hunter/tools.py)): the six tools, the test-first gate and patch extraction.
5. **Agent loop** ([repo_bug_hunter/agent.py](repo_bug_hunter/agent.py)): prompts, pricing and the Claude call.
6. **Task loading and batch runner** ([repo_bug_hunter/tasks.py](repo_bug_hunter/tasks.py), [repo_bug_hunter/run.py](repo_bug_hunter/run.py)).
7. **Grading wrapper** ([repo_bug_hunter/evaluate.py](repo_bug_hunter/evaluate.py)) around the official harness.
8. **Analysis** ([repo_bug_hunter/analyze.py](repo_bug_hunter/analyze.py)): resolve rate, cost, steps, failure categories and statistics.
9. **Replay site** ([repo_bug_hunter/viewer.py](repo_bug_hunter/viewer.py) and [viewer/index.html](viewer/index.html)).
10. **Tests.** We wrote 47 tests and fixed the three bugs they found (see [section 6](#6-bugs-found-and-fixed)).
11. **Visual check.** We built a demo replay site, served it locally and took headless Chrome screenshots at desktop width and at phone width (390px). We fixed a phone layout overflow this way.
12. **README.**

### Step 3: Checking the requirements on the Mac

We checked the machine against what the project needs:

| Requirement | Status |
|---|---|
| Python 3.10+ | 3.13.7 ✅ |
| uv | 0.11.3 ✅ |
| git | 2.54 ✅ |
| Docker Desktop | 28.5.1, installed ✅ |
| Rosetta for x86-64 images (Apple Silicon) | Already on ✅ |
| Disk | 109 GB free ✅ (plan for tens of GB of images) |
| Memory | 16 GB ✅ (8 GB given to Docker) |
| Anthropic API key | ❌ Not wanted, since it costs money |

### Step 4: Making it free with a local model

The original design ran on Claude, which needs a paid API key. The agent loop doesn't really depend on Claude, so we added support for **any OpenAI-compatible endpoint** ([repo_bug_hunter/openai_compat.py](repo_bug_hunter/openai_compat.py)). This covers Ollama, LM Studio, vLLM and hosted providers.

- Of the models already in Ollama, `gemma4` supports tool calling and `llama3` does not.
- Ollama gives models only 4,096 tokens of context by default, which is far too small for an agent. We made **`gemma4-32k`**, a variant with a 32k context window that uses the same weights, so nothing new was downloaded.
- Cost shows as $0 for free models, so the report also shows tokens per issue.
- Running the smoke test with the real local model found two more bugs, which were fixed (see [section 6](#6-bugs-found-and-fixed)). The test count went up to 54.

### Step 5: First real SWE-bench run

We ran the whole pipeline on one real task, `psf__requests-1142` (an easy "<15 min fix" issue in the `requests` library):

1. Started Docker Desktop and pulled the task's image (3.8 GB).
2. Ran the agent with `gemma4-32k` inside the container, with no network.
3. Graded the patch with the official SWE-bench harness.
4. Ran the analysis and built the replay page.

The result is in [section 8](#8-results-so-far).

### Step 6: Checking that everything works, and four fixes (30 Sep)

We re-checked the project from the start and ran the one piece not yet run for real: the **test-first variant on a real SWE-bench task** (`runs/local_pilot_tf`). The rule worked as designed. It blocked the agent's first edit, confirmed that the agent's test really failed on the original code, and re-ran the test at submit. The official grader marked the task resolved (see [section 8](#8-results-so-far)).

Four fixes came out of this check:

- **Resuming retries infrastructure failures.** Tasks that failed because Docker or the model server was down (`env_error`, `api_error`) are now run again when you re-run the same command. Before, they were skipped, and the only way to retry was `--redo`, which re-runs everything.
- **Early warning before a run.** `run` now stops at the start with a clear message if Docker isn't running, the model server can't be reached, or the model name is wrong. Before, a 50-task run would have written 50 failed trajectories.
- **Search errors are flagged.** A `search` that fails because of a bad regex or a missing path is now marked as an error, so the model knows the call failed. In the real test-first run, the agent's very first search used the path `.*`, which doesn't exist.
- **Clearer report and replay page.** The comparison no longer shows a bootstrap interval for fewer than 20 tasks, because with that few tasks the interval is misleading. The replay page now says "nothing ran" on turns with no tool call.

### Step 7: Ready for anyone to use: free models, no local Docker (1 Oct)

The goal changed: the project should be open source and usable by anyone, on their own model or API key, without installing a local model or Docker. Research first (see [section 5](#5-key-design-decisions-and-why)), then these changes:

- **Free OpenRouter models are the default.** [repo_bug_hunter/providers.py](repo_bug_hunter/providers.py) picks the model: OpenRouter (default model `poolside/laguna-s-2.1:free`), Claude, Ollama, or any OpenAI-compatible server. It infers the provider from the model name and checks the key and the model before a run. On OpenRouter it also looks up the model's context window and whether it supports tool calling.
- **Tested for real.** `qwen/qwen3.8-27b:free` has a single upstream provider, which was rate-limited (HTTP 429), so the run failed before its first step. `poolside/laguna-s-2.1:free` fixed the toy bug in 7 steps with a tool call on every turn.
- **Rate limits and daily limits.** Short limits are waited out, about ten minutes in all, and so are empty replies. A daily limit stops the run cleanly, and the next invocation resumes it.
- **No local Docker needed.** [.github/workflows/experiment.yml](.github/workflows/experiment.yml) runs each task in its own job on GitHub's free machines, which have Docker, then merges, reports and publishes the replay site. [.devcontainer/](.devcontainer/devcontainer.json) makes Codespaces work in the browser.
- **Safety found by testing.** The first OpenRouter smoke test ran on the host with the SWE-bench prompt, which says the code is in `/testbed`. The model couldn't find `/testbed`, so it searched the whole disk and listed the home folder. Now the smoke test runs in a container at `/testbed`, `--local` must be asked for explicitly, the prompt names the real directory, and local commands never see API keys.
- **A proper command-line tool.** One `repo-bug-hunter` command with `doctor`, `smoke`, `run`, `evaluate`, `analyze`, `viewer` and `merge`, an MIT license, CI tests on Linux and macOS, and the replay page shipped inside the package.

---

## 3. How it works: the big picture

```
SWE-bench Verified (Hugging Face)
        │  tasks.py: pick N tasks with a fixed seed
        ▼
run.py ── for each task ──► DockerEnv (task's own image, no network)
        │                    + Toolbox (6 tools, optional test-first gate)
        │                    + run_agent loop ◄──► model (providers.py: OpenRouter, Claude, Ollama, …)
        ▼
runs/<name>/trajs/<id>.json   (every step: thinking, text, tool calls, outputs, tokens, cost)
runs/<name>/preds.jsonl       (one patch per task)
        │
        ▼
evaluate.py ──► official SWE-bench harness ──► runs/<name>/eval.json (resolved? which tests?)
        │
        ├──► analyze.py ──► results.md (resolve rate, cost, steps, failure categories, A/B statistics)
        └──► viewer.py  ──► static site (index.html + JSON data) ──► GitHub Pages
```

Each stage writes files to disk, and the next stage reads them. Any stage can be re-run on its own, and a run can be stopped and resumed.

On GitHub Actions the same stages run spread over jobs: one job per task runs the agent and grades it, and a final job merges the results (`repo-bug-hunter merge`), writes the report and publishes the site (see [4.13](#413-running-without-installing-anything)).

---

## 4. How each part is implemented

### 4.1 Tasks: [repo_bug_hunter/tasks.py](repo_bug_hunter/tasks.py)

- Loads the `SWE-bench/SWE-bench_Verified` test split from Hugging Face.
- `select(tasks, n, seed)` sorts tasks by ID, then takes a seeded random sample. **The same `--n` and `--seed` always give the same tasks**, so the baseline and test-first runs are compared on the same set.
- `patch_files()` lists the files a diff touches. It's used to record which files the real ("gold") fix changed.

### 4.2 Environment: [repo_bug_hunter/env.py](repo_bug_hunter/env.py)

- **`DockerEnv`** starts the task's own SWE-bench image (`docker run -d --rm --platform linux/amd64 ... sleep infinity`). This is the same image the grader uses, so "the tests passed for the agent" means the same thing as "the tests passed for the grader".
- Containers run with `--network none`, so the agent can't `pip install` packages or look up the real fix online.
- Every command runs through `docker exec` after activating the `testbed` conda environment, the same way SWE-bench's eval script does.
- Timeouts run **inside** the container (`timeout -k 5 <seconds>`), so a hung test is actually killed rather than just abandoned.
- Files are read with `cat` and written by piping bytes into the container.
- **`LocalEnv`** runs in a plain local folder. It's used only by the unit tests and by `repo-bug-hunter smoke --local`. Environment variables whose names look like credentials (`KEY`, `TOKEN`, `SECRET`, …) are removed before each command, because command output goes to the model.
- `DockerEnv` takes a `platform`: `linux/amd64` for SWE-bench images, which exist only for x86-64, and none (native) for the smoke test's `python:3.12` image.

### 4.3 Tools: [repo_bug_hunter/tools.py](repo_bug_hunter/tools.py)

| Tool | Implementation |
|---|---|
| `bash` | Runs a command in a fresh shell. Default timeout 180s, max 900s. Output over 10,000 characters keeps the first and last 5,000. |
| `read_file` | Returns lines with line numbers, at most 400 lines per call. |
| `search` | `grep -rnIE` over the repository, skipping `.git`, up to 100 hits. A bad regex or a missing path comes back as an error. |
| `edit_file` | Replaces one exact snippet. It refuses when the snippet is missing or appears more than once, and shows the edited lines afterwards. |
| `write_file` | Creates or overwrites a file, creating folders as needed. |
| `submit` | Ends the task. In test-first mode it re-runs the reproduction first. |
| `record_failing_test` | Test-first only. See [4.6](#46-the-experiment-test-first-enforced-by-the-harness). |

Design rules:

- **Tools never crash the run.** Bad arguments, missing files and Docker errors come back to the model as an error message it can act on.
- **Arguments are checked** against each tool's schema: required keys, unknown keys and types.
- **Stale bytecode is removed.** After writing a `.py` file, the matching `__pycache__/*.pyc` is deleted. Without this, Python could run old code (see [section 6](#6-bugs-found-and-fixed)).

**How the patch is built:**

1. When the task starts, `git stash create` records the starting state as the "base", along with the untracked files that already exist. Changes that were already in the Docker image therefore never end up in the patch.
2. At the end, the patch is `git diff --binary` against that base.
3. New files are included only if they look like source code (`.py`, `.cfg`, `.toml` and so on). This keeps out `.pyc` files, plots and other leftovers from test runs, which would break `git apply` in the grader.
4. Test files and reproduction scripts (`test_*`, `*_test.py`, `repro*`, anything under `tests/`) are always left out, because the grader uses its own tests.

### 4.4 The agent loop: [repo_bug_hunter/agent.py](repo_bug_hunter/agent.py)

The agent is a plain loop with no framework, similar in spirit to mini-swe-agent:

```
messages = [issue text]
for step in 1..max_steps:
    reply = model(messages)             # ask for tool calls
    record the step (text, thinking, tool calls, tokens, cost, which model answered)
    if refused            → stop (exit_status = "refusal")
    if no tool call       → tell the model nothing ran, continue
    run each tool call    → send all results back in one message
    if submitted          → stop ("submitted")
    if cost ≥ max_cost    → stop ("cost_limit")
otherwise                 → "step_limit"
```

Details:

- **Prompts.** The system prompt says where the repo is (`/testbed` in the containers), that there is no network, that hidden tests will check the fix, and that changes to existing test files are thrown away. The test-first variant adds one paragraph and nothing else. The path is a parameter because a model told the wrong path goes looking for it everywhere (see [section 6](#6-bugs-found-and-fixed)).
- **"No tool call" nudge.** Small models sometimes write a tool call as text instead of making one. The loop tells them that nothing ran.
- **Cut-off replies.** If a reply hit `max_tokens`, its tool calls may be incomplete, so they are not run.
- **Cost** is computed from list prices in `PRICES`, including cache writes and cache reads. A provider that reports each request's cost (OpenRouter) is taken at its word. Other models count as $0.
- **The trajectory** (everything above, plus the final patch, step count, tool-call count and time taken) is saved as JSON. It's what the analysis and the replay page read.

### 4.5 Model adapters

**Choosing the model** ([repo_bug_hunter/providers.py](repo_bug_hunter/providers.py)): `--model` alone is usually enough. Claude model names go to Claude's API, and `vendor/model` names go to OpenRouter. `--provider ollama` or `--base-url` covers local and other servers. With no `--model` at all, the default is the free `poolside/laguna-s-2.1:free`. Before a run it checks:
- **The key.** It's read from `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY` or `LLM_API_KEY`. A missing key comes with a link to get one.
- **The model.** On OpenRouter, the model must exist and support tool calling. Its context window is looked up, and capped at 128k tokens.

We kept our own two adapters instead of adding LiteLLM. Almost every provider speaks the OpenAI chat format. Claude needs its native API for prompt caching and thinking, which Anthropic's OpenAI-compatible endpoint drops. And LiteLLM's PyPI package was compromised in March 2026.

**Claude** (`claude()` in [repo_bug_hunter/agent.py](repo_bug_hunter/agent.py)):

- Streams each request, with `max_tokens=64000` and up to 8 retries.
- Prompt caching is turned on with one top-level `cache_control`, which moves to the end of the conversation each turn, so each step reuses the previous prefix.
- Adaptive thinking and an `effort` setting (default `medium`) are used for every model except Haiku 4.5, which doesn't support them.
- Refusal fallback (`fallbacks: "default"`): if a safety classifier declines a turn, the API re-runs it on another model. Each step records which model answered, so the report can count runs where this happened, and that turn is priced at the other model's rates.

**OpenAI-compatible** ([repo_bug_hunter/openai_compat.py](repo_bug_hunter/openai_compat.py)):

- The loop keeps its history in Anthropic's message format. This adapter translates it to OpenAI chat messages on the way out and wraps the reply back into the shape the loop expects. **The loop, tools and analysis are identical whichever model runs.**
- **Fitting a small context window.** When the prompt gets close to the window, the oldest tool outputs are replaced with a short note, six at a time. Removing them in chunks keeps the start of the prompt the same between steps, so the server can reuse its cache. Long file contents inside old `edit_file` and `write_file` calls are shortened too.
- Reasoning is read from `reasoning` or `reasoning_content` fields, or from inline `<think>…</think>` tags.
- Invalid JSON in tool arguments becomes a clear error for the model instead of a crash.
- **Rate limits.** A 429 is waited out: 15 s, 30 s, 60 s … about ten minutes in all. Free models hit this per minute, and whenever their upstream provider is busy. A reply with no choices, which OpenRouter sends when an upstream fails, is retried the same way. A **daily** limit is different: OpenRouter's `free-models-per-day`, or any used-up allowance that resets more than 15 minutes later. It raises `DailyLimitReached`, and the runner stops scheduling tasks.
- **Cost.** On OpenRouter each request asks for usage accounting (`usage: {include: true}`), so its cost is known and `--max-cost` works for paid models.
- The reply budget grows with the window: 4k tokens in a 32k window, up to 16k in a large one.

### 4.6 The experiment: test-first, enforced by the harness

The key design choice is that the **harness enforces** test-first rather than just asking for it in the prompt. That makes it a real, measurable variable.

1. **Red.** Source files are locked, so `edit_file` and `write_file` refuse to change them. Test files stay editable. The agent writes a test (usually a standalone script such as `repro_test.py`) and registers its command with `record_failing_test`. The harness runs it on the **unmodified** code:
   - A non-zero exit means the bug is reproduced. The test is recorded and source files unlock.
   - Exit 0 (the test passes) or a timeout means it's not recorded.
   - Exit 126 or 127 (the command couldn't even start) means it's not recorded, because that proves nothing about the bug.
   - If source files were already changed through `bash`, recording is refused until they're reverted.
2. **Green.** On `submit`, the harness re-runs the command. If it still fails, the first submit is rejected and the agent keeps working.
3. **Escape hatches.** After 3 failed recording attempts, source files unlock anyway, because some bugs are hard to reproduce. The second submit is always accepted. Both cases are saved in the trajectory (`repro.status`, `repro.passed_at_submit`), so the analysis can ask whether tasks with a real reproduction were solved more often.

**Only this gate differs** between the two variants. The tasks, model, effort, limits and base prompt are the same.

### 4.7 Runner: [repo_bug_hunter/run.py](repo_bug_hunter/run.py)

- **Checks first.** Before starting, it checks the key, that Docker is running (`docker info`), and that the model is available: on OpenRouter, that it exists and supports tool calling; on another server, that it answers and serves that model. If not, it stops with a clear message.
- Picks tasks (`--n` and `--seed`, `--instances`, or `--difficulty`), then runs them in parallel threads (`--workers`, default 2; use 1 for local models).
- Writes `runs/<name>/config.json`, including the provider and the context window. **It refuses to reuse a run name with a different variant, provider, model or effort**, so results from two setups can't get mixed together.
- **Stops at a daily limit.** When a task ends with `DailyLimitReached`, the tasks still queued are not started. The run says to try again after the limit resets, and resuming picks them up.
- `--print-instances` prints the selected tasks that still need a run, as JSON, and exits. It needs no key and no Docker. The GitHub workflow uses it to plan its jobs.
- **Resumes:** tasks that already have a trajectory are skipped unless `--redo` is passed. Tasks that failed for infrastructure reasons (`env_error`, `api_error`) are always retried.
- If Docker fails, the task is saved with `exit_status = "env_error"`, and the rest of the run continues.
- Saves the files the real fix changed (`gold_files`) with each trajectory, for failure classification later.
- Writes `preds.jsonl` in the format the SWE-bench harness expects. Unfinished runs (`env_error`, `api_error`) are left out, so their partial patches are never graded.

### 4.8 Grading: [repo_bug_hunter/evaluate.py](repo_bug_hunter/evaluate.py)

- Calls the official harness: `python -m swebench.harness.run_evaluation`. Add `--modal` to grade in the cloud.
- Reads each task's `report.json` and records whether it was resolved, which of the fix's tests (`FAIL_TO_PASS`) passed or failed, and whether previously passing tests (`PASS_TO_PASS`) broke.
- A task is **resolved** only if all `FAIL_TO_PASS` tests now pass and all `PASS_TO_PASS` tests still pass.
- It detects empty patches, patches that don't apply, and test timeouts from the harness log.
- `--collect-only` re-reads existing logs without grading again. If every patch is empty (for example, all tasks stopped at a daily limit), the grader isn't started at all.

### 4.9 Analysis: [repo_bug_hunter/analyze.py](repo_bug_hunter/analyze.py)

**Failure categories.** Each unresolved task gets exactly one category. The first rule that matches wins:

1. `run_error`: Docker or API crash. In practice this never appears: `load_run` sets aside runs that ended with `env_error` or `api_error`, including a daily limit, as **not finished**. They aren't counted, the report lists them, and resuming redoes them.
2. `gave_up`: step or cost limit, refusal, or an empty patch
3. `patch_did_not_apply`
4. `eval_error`: the grader produced no test results
5. `wrong_file`: the patch touches none of the files the real fix changed
6. `broke_other_tests`: some `PASS_TO_PASS` tests now fail
7. `tests_still_fail`: right file and nothing broken, but not fixed

**Statistics.** With only 50–100 tasks, chance alone can move the numbers by several points, so the report doesn't stop at two percentages:

- **Wilson 95% confidence interval** for each resolve rate, because it works well at small sample sizes.
- **McNemar's exact test** for the before/after comparison. It is paired, on the same tasks, and uses only the tasks where exactly one variant succeeded.
- **Paired bootstrap** (10,000 resamples) for a 95% interval on the difference. It's shown only with 20 or more shared tasks. With fewer, the report says there are too few tasks for an interval.
- Plain-language verdict: if p ≥ 0.05, the report says the difference could be noise.
- Breakdowns by difficulty and by repository, and for test-first, by whether the bug was reproduced.

The output is a Markdown report (`--out results.md`).

### 4.10 Replay site: [repo_bug_hunter/viewer.py](repo_bug_hunter/viewer.py) and [repo_bug_hunter/site/index.html](repo_bug_hunter/site/index.html)

- `viewer.py` writes `data/manifest.json` (all runs, summaries and the comparison) and one JSON file per task, then copies the single HTML page next to them. The page ships inside the package, so `repo-bug-hunter viewer` also works after `uv tool install`.
- The page is **fully static**: no server and no build step, so it can go on GitHub Pages for free.
- Features:
  - A task list with filters by failure category
  - Step-by-step playback, with arrow-key navigation. Turns with no tool call are labelled "nothing ran".
  - Deep links to a specific step (`#run=test_first&id=django__django-11099&step=12`)
  - A final panel with the grading details and the patch
  - A layout that works on phones
- It was checked with headless Chrome screenshots at desktop and phone (390px) widths.

### 4.11 Smoke test: [repo_bug_hunter/smoke.py](repo_bug_hunter/smoke.py)

A quick end-to-end check of the model setup. It creates a toy git repository with a bug (`median()` is wrong for even-length lists), runs the agent on it, and checks that the bug is fixed. For Claude, it also checks that prompt caching is working.

- **By default** the repository lives at `/testbed` in a throwaway `python:3.12` container with no network, like the real tasks. That image includes git, which patch extraction needs.
- **`--local`** runs without Docker, in a temporary directory on the host. You have to ask for it explicitly. The prompt names that directory, credentials are removed from the commands' environment, and a `python` shim is added because many Macs only have `python3`.
- It exits with 0 if the bug was fixed and 1 if not.

### 4.12 Fresh tasks from GitHub: [repo_bug_hunter/pr.py](repo_bug_hunter/pr.py)

SWE-bench Verified is contaminated for frontier models, so `repo-bug-hunter pr owner/repo#PR` builds a SWE-bench-style task from any merged pull request, as recent as you like:

1. **Issue.** From the GitHub API: the pull request, then the issue it names. Closing keywords are tried first, then "issue 123", then any `#123`, skipping numbers that are pull requests. With no issue it refuses, because the pull request's description would give the fix away. `owner/repo#PR:ISSUE` names the issue by hand.
2. **Image.** `python:3.12` plus a full clone checked out at the merge commit's first parent, which is the code just before the fix. An embedded script installs the project with its test extra or dependency group (`test`, `tests`, `testing`, else `dev`), test requirements files, and pytest. `--install` replaces the script.
3. **Which tests decide.** The fix is split into code and test changes (`is_test_path`). In a container, the test change is applied and the changed test modules are run; then the code change is applied and they run again. Tests that fail and then pass are `FAIL_TO_PASS`, and tests that pass both times are `PASS_TO_PASS`. If nothing goes from failing to passing, the task is refused. The file count is checked against the pull request's, so a rebase-merged pull request is caught instead of mis-built.
4. **Agent.** It runs in a container of the image with no network, exactly like `repo-bug-hunter run`.
5. **Grading.** In a fresh container: the agent's patch, then the fix's test files reset and its test patch applied (as SWE-bench does), then the test modules run with `pytest -rA`. Resolved means every `FAIL_TO_PASS` and `PASS_TO_PASS` test passes.

Every task is prepared before the first agent run, so all GitHub calls happen together and a pull request that can't be used is reported before any model request. Tests run with `--color=no` and escape codes stripped, because some projects force coloured output. Tasks are saved in `runs/<name>/tasks/`, and trajectories and grades in the usual places, so a second invocation resumes and `analyze`/`viewer` work unchanged. Bytecode caches are deleted, and none are written, before every test run: the unit tests caught `git apply` making a same-size edit within a second, which made Python run the old code's `.pyc`.

### 4.13 Running without installing anything

- **[experiment.yml](.github/workflows/experiment.yml)**, started by hand from the Actions tab, has four jobs:
  1. **plan** restores earlier results from the `repo-bug-hunter-results` branch and uses `repo-bug-hunter run --print-instances` to list the tasks still to do.
  2. **solve** is a matrix with one job per task, at most `max_parallel` at once. It frees disk space, runs `repo-bug-hunter run --instances <id>`, grades with `repo-bug-hunter evaluate`, and uploads the run as an artifact.
  3. **report** runs `repo-bug-hunter merge` to combine the artifacts with the earlier results. It writes `results.md` into the job summary, builds the replay site, and commits `runs/<name>/` to `repo-bug-hunter-results`.
  4. **pages** deploys the site.
  Inputs are passed through environment variables, never pasted into scripts. Re-running a name resumes it, which is how a run continues after a daily limit.
- **[repo_bug_hunter/merge.py](repo_bug_hunter/merge.py)** combines trajectories, grades and settings from several places into one run. It refuses to mix runs with different settings. A task that ran again replaces the old trajectory and drops the old grade, because that grade belonged to the old patch.
- **[.devcontainer/devcontainer.json](.devcontainer/devcontainer.json)** sets up a Codespace: Python 3.12, Docker-in-Docker and uv, and it asks for `OPENROUTER_API_KEY` as a secret.
- **[repo_bug_hunter/doctor.py](repo_bug_hunter/doctor.py)** checks Python, Docker, the CPU architecture, free disk, the key, the model and, on OpenRouter, today's free requests and the free models with tool calling.
- **[repo_bug_hunter/cli.py](repo_bug_hunter/cli.py)** is the `repo-bug-hunter` command, which dispatches to each module's `main()`. `python -m repo_bug_hunter.<module>` still works.

---

## 5. Key design decisions and why

| Decision | Why |
|---|---|
| Python | SWE-bench, its harness and mini-swe-agent are all Python. |
| A plain loop, no agent framework | Easy to read and explain, and every step is visible and logged. |
| Run in the grader's own Docker image | The agent's test results match the grader's. |
| No network in containers | The agent can't install packages or look up the real fix. |
| The harness enforces test-first | A prompt suggestion can be ignored. A gate makes the variable real. |
| Only one difference between variants, with a fixed seed | Any change in results comes from the gate alone. |
| Paired statistics and confidence intervals | At 50–100 tasks, differences can easily be noise, and the report says so. |
| Escape hatches in the gate | Some bugs can't easily be reproduced, and the gate shouldn't make those tasks impossible. |
| Static replay site | Free hosting, and recruiters can see real runs. |
| Support for OpenAI-compatible models | Runs for free on a local model, with no paid API key. |
| Every stage writes files | Stages can be re-run on their own, and runs can resume. |
| Free OpenRouter models by default | Anyone can run it without paying. One key covers hundreds of models, including paid ones and Claude. |
| Our own two adapters, not LiteLLM | Claude needs its native API (caching, thinking), everything else speaks the OpenAI format, and LiteLLM's PyPI package was compromised in March 2026. |
| GitHub Actions instead of a hosted service | Free and unlimited for public repositories, with Docker and x86-64 machines. Each user runs it in their own fork with their own key. GitHub's terms allow Actions for a repository's own testing, but not as the backend of a website. |
| Docker Offload, Modal and others not used (yet) | Docker Offload still needs Docker Desktop on each PC. Modal ($30/month free) would need a new environment class; it's the next option for running from a laptop without Docker. |
| The smoke test runs in a container | A model told the wrong path explored the host; see section 6. |

---

## 6. Bugs found and fixed

Testing and the real runs found twelve bugs. Each one would have made the results wrong or the tool unsafe to share.

| Bug | Effect | Fix |
|---|---|---|
| **Stale bytecode** | An edit of the same length made within a second of a test run could keep running the old code, because Python trusted the cached `.pyc`. | Delete the matching `.pyc` after every `.py` write. |
| **Junk in the patch** | `.pyc` files and saved plots from test runs ended up in the patch, which would cause false "patch did not apply" failures. | Only new files with source-like extensions go into the patch. |
| **Changes already in the image** | Changes already present in a Docker image could leak into the patch. | Snapshot the starting state with `git stash create` and diff against it. |
| **Unrunnable commands counted as reproductions** | A test command that couldn't start (no `python` on the Mac) was accepted as a real failing test. | Exit codes 126 and 127 are rejected. The smoke test adds a `python` shim. |
| **Context overflow on small models** | Old tool outputs filled up the 32k window. | The adapter removes the oldest outputs in chunks of 6. |
| **Wrong path in the smoke test** | The prompt said `/testbed` but the toy repo was in a temp folder on the host, so the model searched the whole disk and listed the home folder, which went to the model provider. | The smoke test runs in a container at `/testbed`; the prompt takes the real path; `--local` hides credentials. |
| **Giving up too fast on free models** | A busy upstream provider answered 429, and the client's quick retries ended the task before its first step. | Waits of up to about ten minutes for 429s and empty replies; a daily limit stops the run cleanly for a resume. |
| **Unfinished runs were scored** | In the first multi-task pilot, a task stopped by the daily limit had its partial patch graded as resolved, while the report said such tasks "count as failures". Neither is right for a task that is redone on resume. | Unfinished runs stay out of `preds.jsonl` and out of the statistics, and the report lists them as "not finished". |
| **Stale bytecode in `repo-bug-hunter pr` grading** | The new grading applies patches with `git apply`, which bypassed the agent tools' `.pyc` cleanup. A same-size fix within a second of a test run ran the old code, and a valid task was rejected as "no test fails without the fix". | Test runs delete `__pycache__` first and set `PYTHONDONTWRITEBYTECODE=1`. Found by the new unit tests before any real run. |
| **Coloured test output unread** | humanize's pytest settings force colour, so every result line began with an escape code and none were recognised; the task was refused as "no test fails without the fix". Found by building tasks from 4 real projects. | Tests run with `--color=no`, and escape codes are stripped before parsing. Test ids with spaces and " - " inside `[parameters]` (loguru, humanize) parse correctly too. |
| **A shared network's GitHub limit** | On a university or office network many people share one IP and its 60 unauthenticated API requests an hour; the limit ran out mid-run. | Every task is prepared (all API calls) before any agent runs, so problems show before model requests are spent, and a used-up limit says when it resets and to set `GITHUB_TOKEN`. |
| **Replay page missing from the package** | A `site/` rule in `.gitignore` also matched `repo_bug_hunter/site/`, so the built wheel had no page. | The rule is anchored to the repository root (`/site/`); the wheel was rebuilt and checked. |

---

## 7. Testing

`uv run pytest` runs **106 tests in about half a minute, with no model and no network needed**. Two of them start real Docker containers and are skipped when Docker isn't running. They run against `LocalEnv`, a scripted fake model and fake servers. [tests.yml](.github/workflows/tests.yml) runs them on every push, on Linux and macOS with Python 3.10 and 3.12.

| File | What it covers |
|---|---|
| [tests/test_tools.py](tests/test_tools.py) | Each tool, argument checks, truncation, what goes into the patch, the test-first gate (locking, rejected reproductions, unlocking after 3 attempts, the re-run at submit) |
| [tests/test_agent.py](tests/test_agent.py) | The loop: fixing and submitting, step and cost limits, refusals, the no-tool-call nudge, cut-off replies, pricing (including cache and fallback), request settings |
| [tests/test_openai_compat.py](tests/test_openai_compat.py) | Translating messages both ways, edge cases, invalid JSON, context trimming, a full run through a fake OpenAI-compatible server, waiting out rate limits and empty replies, stopping at a daily limit, OpenRouter's reported cost |
| [tests/test_providers.py](tests/test_providers.py) | Picking the provider from the model name, missing keys, custom servers, the OpenRouter check (context window, tool calling, suggestions) |
| [tests/test_merge.py](tests/test_merge.py) | Merging parallel jobs, replacing retried tasks and their stale grades, refusing to mix models |
| [tests/test_pr.py](tests/test_pr.py) | `repo-bug-hunter pr` against a local "upstream" repository: finding the issue (skipping pull requests), FAIL_TO_PASS and PASS_TO_PASS, grading right, wrong, empty and test-editing patches, and resuming without re-running or re-grading |
| [tests/test_commands.py](tests/test_commands.py) | The `repo-bug-hunter` command, `smoke --local` (and the real path in its prompt), `doctor`, API keys hidden from local commands |
| [tests/test_analyze.py](tests/test_analyze.py) | Failure categories, Wilson interval, McNemar test, bootstrap, the report, building the replay site |
| [tests/test_pipeline.py](tests/test_pipeline.py) | The run → grade → analyze pipeline, refusing to mix settings under one run name, stopping at a daily limit and resuming the next day, planning with `--print-instances`, not starting the grader when there's nothing to grade |

---

## 8. Results so far

Both variants ran on one real task, to check that every stage works. This is not yet a measurement.

Task: `psf__requests-1142` (psf/requests, "<15 min fix"). The issue: `requests.get()` always sends a `Content-Length` header. Model: `gemma4-32k` via Ollama (local, free).

| | Baseline ([runs/local_pilot/](runs/local_pilot/)) | Test-first ([runs/local_pilot_tf/](runs/local_pilot_tf/)) |
|---|---|---|
| Steps | 9 (9 tool calls) | 26 (21 tool calls) |
| Time | ~7 minutes (415s) | ~8 minutes (465s) |
| Tokens | 33k | 197k |
| Cost | $0 | $0 |
| Outcome | Submitted, **not resolved** | Submitted, **resolved** ✅ |
| Failure category | Wrong file | none |

- **Baseline:** the model edited `requests/api.py` and stripped body arguments in `get()`. The real fix is in `requests/models.py`. The grader confirmed that the fix's test (`test_no_content_length`) still failed and that the 5 previously passing tests still passed.
- **Test-first:** the model found `requests/models.py` and tried to edit it at step 7, but the rule blocked it because source files were locked. It then wrote `repro_test.py`, which failed on the original code ("Found unexpected 'Content-Length' header: 0"), and registered it. Next it changed `prepare_content_length()` so no header is added when there is no body. At submit the reproduction passed. The grader confirmed that `test_no_content_length` now passes and all 5 other tests still pass.
- **Honest note:** the agent's fix is simpler than the real one. It also stops sending `Content-Length: 0` for a POST with no body, which the real fix keeps. The hidden tests don't check that case, so the task still counts as resolved. Along the way, 7 of its edit attempts failed because the text it wanted to replace didn't match the file exactly, and 5 of its 26 turns had no tool call.
- **What it means:** on one task, McNemar p = 1.0. That is expected, because one task can't show anything statistically. The report says so. At least 20 tasks per variant are needed before the comparison means much.

**The free OpenRouter model on the same task (1 Oct, [runs/openrouter_pilot/](runs/openrouter_pilot/)).** Baseline with `poolside/laguna-s-2.1:free`, and a step limit of 25 instead of 50 to save the day's free requests.

| | Result |
|---|---|
| Steps | 25 (25 tool calls, none written as text, no tool errors) |
| Time | ~6 minutes (376s) |
| Tokens / cost | 131k / $0 |
| Outcome | Hit the step limit, **not resolved**; category "gave up" |

- It went straight to the right file, `requests/models.py`, and edited `prepare_content_length()` at step 4. The local Gemma baseline had edited the wrong file.
- The logic came out backwards: it adds `Content-Length: 0` to GET and HEAD requests with no body, the opposite of what the issue asks. It also dropped the `seek` that measures a file body. The grader confirmed that `test_no_content_length` still fails and that the 5 other tests still pass.
- It spent its remaining steps checking whether the tests import another copy of the package (`build/lib`), and ran out of steps before submitting.
- The stages all worked as they will on GitHub Actions: plan, run in the task's container, grade with the official harness, merge, report, and replay site (checked with a headless Chrome screenshot).

**The first multi-task pilot with the free model (1 Oct, [runs/free_pilot/](runs/free_pilot/)).** `repo-bug-hunter run --name free_pilot --n 5 --difficulty "<15 min fix"`, with a step limit of 50. The 5 tasks come from 3 repositories (Django, matplotlib, pytest). Today's 50 free requests covered about 2 tasks.

| Task | Steps | Outcome |
|---|---|---|
| `django__django-11119` | 26 (~3 min, 226k tokens) | Submitted, **resolved** ✅. The one-line fix (`Context(context, autoescape=self.autoescape)`) is the real fix. All 7 other tests still pass. |
| `django__django-15104` | 34 (~5 min) | **Not finished**: stopped by the daily limit. The partial patch (`deconstruction[2].pop('to', None)`) already fixed the bug; the grader passed it with all 131 other tests. It doesn't count until it's redone on resume. |
| 3 more tasks | – | Not started: the daily limit stopped the run cleanly. |

- **Rate limits:** about ten short rate limits were waited out (15 s, once 30 s), and all recovered.
- **The daily limit:** OpenRouter's real error said "Rate limit exceeded: free-models-per-day…". It was detected as a daily limit, with its real reset time ("about 14.4 hours"), and the run stopped and said how to resume.
- **What it means:** 1 of 1 finished tasks resolved. The confidence interval (21%–100%) is still far too wide to say anything about the model. The pilot shows that the full pipeline works on several real tasks across repositories, and that a free model can solve real bugs.

---

## 9. What's left to do

1. **Create the GitHub repository and push** (needs your GitHub account). Then:
   - Add the `OPENROUTER_API_KEY` secret.
   - Set Pages to "GitHub Actions".
2. **Pilot on GitHub Actions:** run the `experiment` workflow with the defaults (5 easy tasks).
3. **Buy $10 of OpenRouter credits once** before the full experiment. Without them, 50 requests a day is about 2 tasks. With them, 1,000 requests is about 50 tasks a day. The credits aren't spent on free models.
4. **The experiment:** `baseline` and `test_first` on the same 50 tasks (same `n`, `seed` and model), with `compare_with: baseline` on the second run.
5. **Write the results** into the README from `results.md`, and link the replay site.
6. **Rotate the OpenRouter key** used during development: it was pasted into a chat, and it expires on 7 Oct anyway.

Next options:
- A Modal environment, so a laptop without Docker can run real tasks.
- The public set of SWE-bench Pro, because SWE-bench Verified is contaminated for frontier models.
- A page comparing free models on the same tasks.

Not yet run for real: the Claude path (no API key; covered by tests) and cloud grading on Modal.

---

## 10. Changes made on this Mac

| Change | How to undo |
|---|---|
| Started Docker Desktop and Ollama (again on 30 Sep, after both had stopped) | Quit them |
| Pulled a 3.8 GB SWE-bench image | `docker rmi swebench/sweb.eval.x86_64.psf_1776_requests-1142` |
| Created the `gemma4-32k` model in Ollama | `ollama rm gemma4-32k` |
| The test runs left `runs/local_pilot/`, `runs/local_pilot_tf/` and `logs/` | Delete them if you don't need the examples (the learning guide uses them) |
| Started Docker Desktop again (1 Oct) | Quit it |
| Pulled `python:3.12` (1.6 GB) for the smoke test | `docker rmi python:3.12` |
| The OpenRouter runs left `runs/openrouter_pilot/` and `runs/free_pilot/` (kept as examples) | Delete them if you don't need them |
| Pulled SWE-bench images for `django__django-11119` and `django__django-15104` | `docker rmi` them (see `docker images swebench/*`) |

---

## 11. File map

```
repo_bug_hunter/cli.py            the `repo-bug-hunter` command
repo_bug_hunter/demo.py           replay the example runs in the browser, with no key or Docker
repo_bug_hunter/setup.py          save the model and API key once
repo_bug_hunter/config.py         where saved settings live, and how commands load them
repo_bug_hunter/doctor.py         checks Docker, disk, the model and its key
repo_bug_hunter/providers.py      which model: OpenRouter (default), Claude, Ollama, any OpenAI-compatible server
repo_bug_hunter/agent.py          the loop, prompts, pricing, Claude request settings
repo_bug_hunter/openai_compat.py  OpenAI-compatible models; rate limits, daily limits, reported costs
repo_bug_hunter/tools.py          the tools, the test-first gate, patch extraction
repo_bug_hunter/env.py            Docker environment (and a local one for tests and smoke --local)
repo_bug_hunter/tasks.py          loading SWE-bench Verified, seeded task selection
repo_bug_hunter/run.py            run the agent over a sample of tasks
repo_bug_hunter/pr.py             tasks from merged GitHub pull requests, graded with the fix's own tests
repo_bug_hunter/evaluate.py       grade with the official harness
repo_bug_hunter/analyze.py        resolve rate, cost, steps, failure categories, statistics
repo_bug_hunter/viewer.py         build the static replay site
repo_bug_hunter/site/index.html   the replay page
repo_bug_hunter/merge.py          combine results from parallel jobs
repo_bug_hunter/smoke.py          end-to-end check on a toy bug
.github/workflows/                experiment.yml (runs on GitHub), tests.yml (CI)
.devcontainer/                    Codespaces setup
tests/                            106 tests
runs/<name>/                      config.json, trajs/*.json, preds.jsonl, eval.json
logs/                             SWE-bench harness logs (git-ignored)
```
